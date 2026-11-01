"""Primary-ready startup and blocking profile plugin regression contracts."""
import ast
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def methods(path, names):
    tree = ast.parse((ROOT / path).read_text())
    selected = [node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names]
    namespace = {"asyncio": asyncio, "logging": logging, "logger": logging.getLogger("profile-startup-test"),
                 "Dict": dict, "Path": Path}
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, "exec"), namespace)
    return namespace


class PrimaryReadyStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_ready_is_nonblocking_but_secondary_only_keeps_verdict(self):
        ns = methods("gateway/run_startup.py", {
            "_start_or_defer_secondary_profiles", "_start_secondary_profiles_after_primary"})

        class Runner:
            _start_or_defer_secondary_profiles = ns["_start_or_defer_secondary_profiles"]
            _start_secondary_profiles_after_primary = ns["_start_secondary_profiles_after_primary"]

            def __init__(self, fatal=False):
                self.started, self.finish = asyncio.Event(), asyncio.Event()
                self.fatal, self.stopped, self.task = fatal, False, None

            async def _start_secondary_profiles(self, count, skipped):
                self.started.set()
                await self.finish.wait()
                return self.fatal, count + 1

            def _spawn_supervised(self, factory, name, *, restart):
                self.task = asyncio.create_task(factory())
                return self.task

            async def stop(self):
                self.stopped = True

        primary = Runner()
        self.assertEqual(await primary._start_or_defer_secondary_profiles(2, []), (False, 2))
        await asyncio.wait_for(primary.started.wait(), 2)
        self.assertFalse(primary.task.done())
        await asyncio.sleep(0)  # cron/control-loop turns remain schedulable
        primary.finish.set()
        await primary.task

        secondary_only = Runner()
        verdict = asyncio.create_task(secondary_only._start_or_defer_secondary_profiles(0, []))
        await asyncio.wait_for(secondary_only.started.wait(), 2)
        self.assertFalse(verdict.done())
        self.assertIsNone(secondary_only.task)
        secondary_only.finish.set()
        self.assertEqual(await verdict, (False, 1))

        fatal = Runner(fatal=True)
        fatal.finish.set()
        await fatal._start_or_defer_secondary_profiles(1, [])
        await fatal.task
        self.assertTrue(fatal.stopped)

    async def test_profile_plugin_discovery_runs_off_loop_with_owner_scope(self):
        ns = methods("gateway/run_adapters.py", {"_load_secondary_profile_config"})
        scope = ContextVar("profile_owner", default=None)
        entered, release = threading.Event(), threading.Event()
        observed = []
        loop_thread = threading.get_ident()

        @contextmanager
        def profile_scope(home, *, hydrate_secrets):
            token = scope.set(str(home))
            try:
                yield
            finally:
                scope.reset(token)

        def discover():
            observed.append((threading.get_ident(), scope.get()))
            entered.set()
            release.wait(2)

        package_names = ["gateway", "hermes_cli"]
        module_names = ["gateway.run", "gateway.config", "hermes_cli.env_loader", "hermes_cli.plugins"]
        mods = {name: types.ModuleType(name) for name in package_names + module_names}
        for name in package_names:
            mods[name].__path__ = []
        mods["gateway.run"].MultiplexConfigError = RuntimeError
        mods["gateway.run"]._load_gateway_config = lambda: {}
        mods["gateway.run"]._own_policy_open_startup_violation = lambda config: None
        mods["gateway.run"]._profile_runtime_scope = profile_scope
        expected = object()
        mods["gateway.config"].load_gateway_config = lambda: expected
        mods["hermes_cli.env_loader"].hydrate_profile_secret_sources = lambda home: None
        mods["hermes_cli.plugins"].discover_plugins = discover
        mods["hermes_cli.plugins"].get_plugin_manager = lambda: object()
        runner = types.SimpleNamespace(
            _subscribe_plugin_rewire=lambda *args: None,
            _register_config_hooks=lambda *args, **kwargs: None,
            _snapshot_profile_busy_modes=lambda *args: None)
        with patch.dict(sys.modules, mods):
            task = asyncio.create_task(ns["_load_secondary_profile_config"](
                runner, "owner-a", Path("/tmp/owner-a")))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(entered.is_set())
                self.assertFalse(task.done())
                self.assertEqual(observed[0][1], "/tmp/owner-a")
                self.assertNotEqual(observed[0][0], loop_thread)
            finally:
                release.set()
            self.assertIs(await task, expected)
        self.assertIsNone(scope.get())
