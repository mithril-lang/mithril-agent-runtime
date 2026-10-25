"""The preview.6 bridge must enter its registered interpreter and bootstrap."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_bridge_keeps_profile_arguments_and_enters_the_old_bootstrap(tmp_path):
    old = tmp_path / "old runtime"
    launcher = old / ".hermes/bin/hermes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1:]==['--print-runtime-command']\nprint(json.dumps([sys.executable]))\n")
    launcher.chmod(0o755)
    (old / "hermes_bootstrap.py").write_text("BOOTED = True\n")
    wrapper = tmp_path / "wrapper"
    (wrapper / "hermes_cli").mkdir(parents=True)
    script = wrapper / "mithril-update-bootstrap.py"
    source = Path(__file__).resolve().parents[1] / "scripts/mithril-update-bootstrap.py"
    shutil.copy2(source, script)
    (wrapper / "hermes_cli/mithril_updates.py").write_text(
        "import json,sys,hermes_bootstrap\nprint(json.dumps({'booted':hermes_bootstrap.BOOTED,'args':sys.argv[1:]}))\n")
    result = subprocess.run([sys.executable, "-I", str(script), "-p", "work profile", "update", "--check"],
                            env=dict(os.environ, MITHRIL_RUNTIME_ROOT=str(old)), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"booted": True, "args": ["-p", "work profile", "update", "--check"]}
