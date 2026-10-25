"""Qualify the exact owned CircleCI checkout through its real shell installer.

No signing/account credentials, host configuration or live Profile are passed
to the installed process. This is an isolated candidate check, not publication.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/mithril-install-check")
EVIDENCE = OUT / "evidence"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main():
    if (os.environ.get("CIRCLECI") != "true"
            or os.environ.get("CIRCLE_PROJECT_USERNAME") != "mithril-lang"
            or os.environ.get("CIRCLE_PROJECT_REPONAME") != "mithril-agent-runtime"):
        raise RuntimeError("Installation qualification requires the owned CircleCI project")
    revision = git(ROOT, "rev-parse", "HEAD")
    if revision != os.environ.get("CIRCLE_SHA1") or git(ROOT, "status", "--porcelain"):
        raise RuntimeError("Installation qualification requires the exact clean CI source")
    render = runpy.run_path(str(ROOT / "scripts/build-mithril-release.py"))["render_installer"]
    data, bootstrap_sha = render(revision, branch=os.environ["CIRCLE_BRANCH"])
    OUT.mkdir(exist_ok=False)
    EVIDENCE.mkdir()
    home, runtime, bindir = OUT / "home", OUT / "runtime", OUT / "bin"
    home.mkdir(mode=0o700)
    installer = OUT / "install-mithril.sh"
    installer.write_bytes(data)
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if k in os.environ}
    env.update(HOME=str(home), HERMES_HOME=str(home / ".mithril"), MITHRIL_AGENT="1", CI="true")
    try:
        with (EVIDENCE / "install.log").open("w") as log:
            subprocess.run(["sh", str(installer), "--home", str(home / ".mithril"),
                            "--dir", str(runtime), "--bin-dir", str(bindir)],
                           env=env, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        if git(runtime, "rev-parse", "HEAD") != revision:
            raise RuntimeError("Installed source differs from the qualified checkout")
        expected = hashlib.sha256((ROOT / ".mithril-runtime-provenance.json").read_bytes()).hexdigest()
        if hashlib.sha256((runtime / ".mithril-runtime-provenance.json").read_bytes()).hexdigest() != expected:
            raise RuntimeError("Installed provenance differs from the qualified checkout")
        with (EVIDENCE / "help.log").open("w") as log:
            subprocess.run([str(bindir / "mithril"), "--help"], env=env, check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=120)
        receipt = {"provider": "circleci", "source_revision": revision,
                   "pipeline_id": os.environ["CIRCLE_PIPELINE_ID"],
                   "workflow_id": os.environ["CIRCLE_WORKFLOW_ID"],
                   "job_id": os.environ["CIRCLE_WORKFLOW_JOB_ID"],
                   "installer_sha256": hashlib.sha256(data).hexdigest(),
                   "bootstrap_sha256": bootstrap_sha, "provenance_sha256": expected,
                   "fresh_home_install": True, "actual_help": True, "credentials_forwarded": False}
        (EVIDENCE / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True))
    except Exception:
        log = EVIDENCE / "install.log"
        if log.exists():
            print("\n".join(log.read_text(errors="replace").splitlines()[-60:]))
        raise


if __name__ == "__main__":
    main()
