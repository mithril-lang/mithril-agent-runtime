"""The release builder must not sign from a local or feature-branch invocation."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("ci,branch", [("false", "main"), ("true", "feature")])
def test_signer_refuses_outside_circleci_main(tmp_path, ci, branch):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, CIRCLECI=ci, CIRCLE_BRANCH=branch)
    env.pop("MITHRIL_CLI_UPDATE_SIGNING_KEY", None)
    result = subprocess.run([sys.executable, str(root / "scripts/build-mithril-release.py"),
                             "--version", "0.1.0-preview.7", "--sequence", "7",
                             "--previous-revision", "a" * 40, "--out", str(tmp_path / "artifacts")],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "CircleCI main workflow" in result.stderr
    assert not (tmp_path / "artifacts").exists()
