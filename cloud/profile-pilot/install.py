"""Apply the reviewed placement overlay to a pinned, isolated runtime image."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import sys


def inject(root, candidates, function, decorator):
    for relative in candidates:
        path = root / relative
        if not path.is_file():
            continue
        source = path.read_text()
        tree = ast.parse(source)
        scope = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AIAgent"), tree) if function == "__init__" else tree
        found = [node for node in ast.walk(scope) if isinstance(node, ast.FunctionDef) and node.name == function]
        if len(found) != 1:
            continue
        node = found[0]
        before = hashlib.sha256(source.encode()).hexdigest()
        lines = source.splitlines(keepends=True)
        start = min([node.lineno] + [n.lineno for n in node.decorator_list]) - 1
        lines.insert(start, " " * node.col_offset + "@" + decorator + "\n")
        tree = ast.parse("".join(lines))
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
        if not imports:
            raise ValueError("No safe import insertion point")
        lines.insert(imports[0].lineno - 1, "from hermes_cli.profile_placement import " + decorator + "\n")
        changed = "".join(lines)
        ast.parse(changed)
        path.write_text(changed)
        path.chmod(0o444)
        return {"path": relative, "before": before, "after": hashlib.sha256(changed.encode()).hexdigest()}
    raise ValueError("Pinned image does not expose the expected runtime entrypoint: " + function)


def main(bundle, location):
    root = Path("/opt/hermes")
    if Path("/tmp/pilot-ready").exists():
        print(json.dumps({"installed": True, "source": bundle["source"]}))
        return
    metadata = []
    for relative in ("hermes_cli/profile_placement.py",):
        data = base64.b64decode(bundle["files"][relative], validate=True)
        path = root / relative
        path.write_bytes(data)
        path.chmod(0o444)
    # Keep support for both the factored and older monolithic image entrypoints.
    if (root / "agent/agent_init.py").is_file():
        metadata.append(inject(root, ["agent/agent_init.py"], "init_agent", "guard_turn"))
    else:
        metadata.append(inject(root, ["run_agent.py"], "__init__", "guard_turn"))
    metadata.append(inject(root, ["agent/turn_facade.py", "run_agent.py"], "run_conversation", "guard_turn"))
    metadata.append(inject(root, ["cron/scheduler.py"], "run_one_job", "guard_job"))
    if (root / "cron/scheduler_tick.py").is_file():
        metadata.append(inject(root, ["cron/scheduler_tick.py"], "_tick_admitted", "guard_tick"))
    else:
        metadata.append(inject(root, ["cron/scheduler.py"], "tick", "guard_tick"))
    runtime = base64.b64decode(bundle["files"]["cloud/profile-pilot/runtime.py"], validate=True)
    path = Path("/tmp/pilot-runtime.py")
    path.write_bytes(runtime)
    path.chmod(0o444)
    Path("/run/mithril-runtime-location").write_text(location)
    Path("/run/mithril-runtime-location").chmod(0o444)
    Path("/tmp/pilot-overlay.json").write_text(json.dumps({"source": bundle["source"], "files": bundle["hashes"], "patches": metadata}))
    Path("/tmp/pilot-ready").write_text("ready")
    print(json.dumps({"installed": True, "source": bundle["source"], "patches": metadata}))


if __name__ == "__main__":
    main(json.loads(base64.b64decode(sys.argv[1], validate=True)), sys.argv[2])
