"""Build signed CLI assets in the qualified CircleCI main workflow.

No private key is generated or written here. The release owner projects the
existing 1Password key into this job. This script does not upload/publish assets.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_cli import mithril_updates as updates


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE)


def render_installer(revision, *, branch="main"):
    """Exact installer bytes shared by isolated qualification and release signing."""
    if not updates.SHA.fullmatch(revision):
        raise ValueError("Installer source must be immutable")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch) or ".." in branch:
        raise ValueError("Installer branch is invalid")
    installer = (ROOT / "scripts/install-mithril.sh").read_text()
    installer, count = re.subn(r"(?m)^revision='[0-9a-f]{40}'$", f"revision='{revision}'", installer)
    if count != 1:
        raise ValueError("Installer runtime pin is ambiguous")
    installer, count = re.subn(r"(?m)^branch='main'$", f"branch='{branch}'", installer)
    if count != 1:
        raise ValueError("Installer branch pin is ambiguous")
    bootstrap_sha = hashlib.sha256((ROOT / "scripts/install.sh").read_bytes()).hexdigest()
    installer, count = re.subn(r"(?m)^installer_sha256='[0-9a-f]{64}'$", f"installer_sha256='{bootstrap_sha}'", installer)
    if count != 1:
        raise ValueError("Installer bootstrap hash is ambiguous")
    return installer.encode(), bootstrap_sha


def build(version, sequence, previous, out):
    if os.environ.get("CIRCLECI") != "true" or os.environ.get("CIRCLE_BRANCH") != "main":
        raise ValueError("Release signing requires the CircleCI main workflow")
    if (os.environ.get("CIRCLE_PROJECT_USERNAME") != "mithril-lang"
            or os.environ.get("CIRCLE_PROJECT_REPONAME") != "mithril-agent-runtime"):
        raise ValueError("Release signing requires the owned runtime project")
    revision = git("rev-parse", "HEAD").decode().strip()
    if revision != os.environ.get("CIRCLE_SHA1") or git("status", "--porcelain").strip():
        raise ValueError("Release source must be the exact clean CircleCI checkout")
    remote_main = git("ls-remote", "origin", "refs/heads/main").decode().split()
    if not remote_main or remote_main[0] != revision:
        raise ValueError("Runtime main advanced; qualify the current source before signing")
    if not updates.VERSION.fullmatch(version) or sequence < 1:
        raise ValueError("Invalid preview release identity")
    if not previous or any(not updates.SHA.fullmatch(p) for p in previous) or revision in previous:
        raise ValueError("Release requires its qualified previous runtime revisions")
    schema_files = ("hermes_state_schema.py", "hermes_state_common.py")
    schema_hashes = {name: hashlib.sha256(git("show", revision + ":" + name)).hexdigest() for name in schema_files}
    for prior in previous:
        if any(hashlib.sha256(git("show", prior + ":" + name)).hexdigest() != schema_hashes[name] for name in schema_files):
            raise ValueError("A state-schema change requires separate migration and rollback qualification")
    for name in ("CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_WORKFLOW_JOB_ID"):
        if not re.fullmatch(r"[0-9a-f-]{36}", os.environ.get(name, "")):
            raise ValueError("CircleCI release job identity is missing")
    trust = updates.load_trust()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["MITHRIL_CLI_UPDATE_SIGNING_KEY"], validate=True))
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    matching = [key_id for key_id, key in trust["keys"].items()
                if key.get("revoked") is False and key.get("public_key") == public]
    if len(matching) != 1:
        raise ValueError("CircleCI signer does not match exactly one approved CLI public root")
    data, bootstrap_sha = render_installer(revision)
    provenance_bytes = (ROOT / ".mithril-runtime-provenance.json").read_bytes()
    provenance = json.loads(provenance_bytes)
    out.mkdir(parents=True, exist_ok=False)
    archive = out / "mithril-cli-runtime.tar.gz"
    with archive.open("wb") as stream:
        subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar.gz", "--prefix=mithril-cli-runtime/", revision], stdout=stream, check=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {"schema": 1, "product": "mithril-cli", "repository": updates.REPOSITORY,
               "channel": "preview", "version": version, "sequence": sequence,
               "runtime_revision": revision, "previous_revisions": previous,
               "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(),
               "installer": {"url": f"https://github.com/{updates.REPOSITORY}/releases/download/v{version}/install-mithril.sh",
                             "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
               "source_archive": {"url": f"https://github.com/{updates.REPOSITORY}/releases/download/v{version}/mithril-cli-runtime.tar.gz",
                                  "size": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
               "provenance": {"source_repository": "mithril-lang/mithril-fund",
                              "source_revision": provenance["sourceRevision"],
                              "manifest_sha256": hashlib.sha256(provenance_bytes).hexdigest()}}
    envelope = {"signed": payload, "key_id": matching[0],
                "signature": base64.b64encode(private.sign(updates.canonical(payload))).decode()}
    updates.verify(envelope, trust)
    (out / "install-mithril.sh").write_bytes(data)
    (out / "mithril-update.json").write_bytes(updates.canonical(envelope) + b"\n")
    evidence = {"schema": 1, "provider": "circleci", "runtime_revision": revision,
                "version": version, "key_id": matching[0],
                "public_key_sha256": hashlib.sha256(base64.b64decode(public)).hexdigest(),
                "pipeline_id": os.environ["CIRCLE_PIPELINE_ID"],
                "workflow_id": os.environ["CIRCLE_WORKFLOW_ID"],
                "job_id": os.environ["CIRCLE_WORKFLOW_JOB_ID"],
                "bootstrap_sha256": bootstrap_sha}
    evidence["unchanged_state_schema_sha256"] = schema_hashes
    (out / "release-evidence.json").write_bytes(updates.canonical(evidence) + b"\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(out.iterdir())]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(f"Built signed Mithril CLI {version} from {revision}; key {matching[0]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--previous-revision", action="append", required=True,
                        help="Compatible exact runtime revisions; repeat or comma-separate")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    previous = [revision for group in args.previous_revision for revision in group.split(",")]
    build(args.version, args.sequence, previous, args.out)


if __name__ == "__main__":
    main()
