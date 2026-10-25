"""Enter the wrapper's signed updater using preview.6's published PM boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(os.environ["MITHRIL_RUNTIME_ROOT"]).resolve(strict=True)
    runtime = root / ".hermes/bin/hermes"
    # Resolve the installation's registered interpreter; do not scrape shell
    # launchers, capture a transient venv generation, or use ambient PYTHONPATH.
    command = json.loads(subprocess.check_output([str(runtime), "--print-runtime-command"], text=True, timeout=30))
    if not isinstance(command, list) or not command or not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
        raise RuntimeError("The installed runtime did not resolve its PM interpreter")
    control = Path(__file__).resolve().parent / "hermes_cli/mithril_updates.py"
    if not control.is_file():
        raise RuntimeError("The Homebrew wrapper has no qualified update verifier")
    code = ("import sys,runpy; sys.path.insert(0," + repr(str(root)) + "); "
            "import hermes_bootstrap; runpy.run_path(" + repr(str(control)) + ",run_name='__main__')")
    os.execv(command[0], [command[0], "-I", "-c", code, *sys.argv[1:]])


if __name__ == "__main__":
    main()
