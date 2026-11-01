"""Exercise gateway startup functions without loading providers or opening sockets."""
import ast
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import logging
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "gateway/run.py"


def functions():
    wanted = {"_discover_gateway_mcp_tools", "_start_gateway_mcp_discovery"}
    tree = ast.parse(SOURCE.read_text())
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in wanted]
    namespace = {"asyncio": asyncio, "copy_context": copy_context, "Path": Path,
                 "logger": logging.getLogger("startup-test")}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


class GatewayCronStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = functions()
        self.home = ContextVar("test_home", default=None)
        self.oauth = ContextVar("test_oauth", default=False)
        self.modules = {
            "tools": types.ModuleType("tools"),
            "tools.mcp_oauth": types.ModuleType("tools.mcp_oauth"),
            "tools.mcp_tool_discovery": types.ModuleType("tools.mcp_tool_discovery"),
        }
        self.modules["tools"].__path__ = []

        @contextmanager
        def suppress():
            token = self.oauth.set(True)
            try:
                yield
            finally:
                self.oauth.reset(token)
        self.modules["tools.mcp_oauth"].suppress_interactive_oauth = suppress
        self.patch = patch.dict(sys.modules, self.modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_slow_secret_hydration_does_not_block_gateway_loop(self):
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        scope_threads = []

        @contextmanager
        def scope(home):
            scope_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(0.5):
                raise TimeoutError("secret helper blocked the event loop")
            yield

        self.ns["_profile_runtime_scope"] = scope
        self.ns["_multiplex_profile_homes"] = lambda config: [("a", "/tmp/profile-a")]
        self.modules["tools.mcp_tool_discovery"].discover_mcp_tools = lambda: None
        task = asyncio.create_task(self.ns["_discover_gateway_mcp_tools"](
            types.SimpleNamespace(multiplex_profiles=True)))
        try:
            for _ in range(30):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            self.assertTrue(entered.is_set())
            self.assertFalse(task.done(), "a blocked helper must not consume the gateway loop")
            self.assertNotEqual(scope_threads, [loop_thread])
        finally:
            release.set()
            await task

    async def test_profile_and_oauth_scopes_survive_worker_handoff_a_b_a(self):
        observed = []

        @contextmanager
        def scope(home):
            token = self.home.set(str(home))
            try:
                yield
            finally:
                self.home.reset(token)

        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            self.ns["_profile_runtime_scope"] = scope
            self.ns["_multiplex_profile_homes"] = lambda config: [("a", a), ("b", b), ("a", a)]
            self.modules["tools.mcp_tool_discovery"].discover_mcp_tools = (
                lambda: observed.append((self.home.get(), self.oauth.get())))
            await self.ns["_discover_gateway_mcp_tools"](
                types.SimpleNamespace(multiplex_profiles=True))
            self.assertEqual(observed, [(str(a), True), (str(b), True), (str(a), True)])
        self.assertIsNone(self.home.get())
        self.assertFalse(self.oauth.get())

    async def test_broken_profile_does_not_prevent_sibling_discovery(self):
        observed = []

        @contextmanager
        def scope(home):
            if str(home).endswith("broken"):
                raise RuntimeError("profile source unavailable")
            yield

        self.ns["_profile_runtime_scope"] = scope
        self.ns["_multiplex_profile_homes"] = lambda config: [
            ("broken", "/tmp/broken"), ("healthy", "/tmp/healthy")]
        self.modules["tools.mcp_tool_discovery"].discover_mcp_tools = (
            lambda: observed.append("healthy"))
        with self.assertLogs("startup-test", level="WARNING"):
            await self.ns["_discover_gateway_mcp_tools"](
                types.SimpleNamespace(multiplex_profiles=True))
        self.assertEqual(observed, ["healthy"])

    async def test_slow_mcp_warmup_is_retained_without_blocking_cron(self):
        started, finish = asyncio.Event(), asyncio.Event()
        retained = set()

        async def slow(config):
            started.set()
            await finish.wait()

        class Runner:
            config = object()

            def _spawn_supervised(self, factory, name, *, restart):
                self_task = asyncio.create_task(factory())
                retained.add(self_task)
                self_task.add_done_callback(retained.discard)
                return self_task

        self.ns["_discover_gateway_mcp_tools"] = slow
        self.ns["_start_gateway_mcp_discovery"](Runner())
        await asyncio.wait_for(started.wait(), 0.5)
        heartbeats = []
        for _ in range(3):
            await asyncio.sleep(0)
            heartbeats.append("tick")
        self.assertEqual(len(heartbeats), 3)
        self.assertEqual(len(retained), 1)
        # The existing runner owns shutdown cancellation; no detached warm-up task remains.
        tasks = list(retained)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertFalse(retained)


if __name__ == "__main__":
    from test_gateway_primary_ready import PrimaryReadyStartupTests
    unittest.main(verbosity=2)
