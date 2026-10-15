#!/usr/bin/env python3
"""Export a verified runtime snapshot without repository history or operator state."""

import argparse
import gzip
import hashlib
import io
import json
import posixpath
import re
import tarfile
import subprocess
from pathlib import Path


EXCLUDED_ROOTS = {
    ".github", "Formula", "contributors", "docker", "docs", "evals", "nix",
    "tests", "tests-js", "website", "workflows",
}
EXCLUDED_NAMES = {"AGENTS.md", "case-study.md", ".envrc"}
SECRET_PATTERNS = (
    re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}\b"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(rb"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"),
    re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    re.compile(rb"\bhf_[A-Za-z0-9]{24,}\b"),
    re.compile(rb"\bxox[bp]-[A-Za-z0-9-]{20,}\b"),
)
REQUIRED = {
    "LICENSE", "pyproject.toml", "uv.lock", "hermes_bootstrap.py", "run_agent.py",
    "scripts/install.sh", "scripts/install.ps1", "hermes_cli/main.py",
    "hermes_cli/skin_engine.py", "hermes_cli/data/mithril-skin.json", "hermes_cli/mithril_auth.py",
}


def selected(path, include_docker=False):
    if path in {"scripts/install-mithril.sh", "scripts/mithril-brew-launcher", ".mailmap"}:
        return False
    parts = path.split("/")
    excluded = EXCLUDED_ROOTS - {"docker"} if include_docker else EXCLUDED_ROOTS
    if parts[0] in excluded or any(part in EXCLUDED_NAMES for part in parts):
        return False
    if any(part in {"tests", "tests-js", "__pycache__", ".git"} for part in parts):
        return False
    if any(part.startswith(".env") and part != ".env.example" for part in parts):
        return False
    if len(parts) == 1 and path.endswith(".md") and path not in {"LICENSE", "SOUL.md", "SECURITY.md"}:
        return False
    return True


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def export_runtime(source_archive, source_tree, revision, destination, include_docker=False, controller_root=None, controller_revision=None):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("An immutable source revision is required")
    if source_tree.get("truncated"):
        raise ValueError("A complete source tree is required")
    expected = {entry["path"]: entry for entry in source_tree["tree"] if entry["type"] == "blob" and selected(entry["path"], include_docker)}
    if not REQUIRED <= expected.keys():
        raise ValueError("Required runtime inputs are missing")
    if include_docker and not {"Dockerfile", "docker/build_agent.py", "docker/hermes-exec-shim.sh", "docker/entrypoint-dispatch.sh"} <= expected.keys():
        raise ValueError("Required container runtime inputs are missing")
    files = {}
    prefix = None
    with tarfile.open(source_archive, "r:gz") as archive:
        for member in archive:
            raw = member.name.rstrip("/")
            parts = raw.split("/")
            if not raw or raw.startswith("/") or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("Unsafe source archive path")
            if prefix is None:
                prefix = parts[0]
            if parts[0] != prefix:
                raise ValueError("Mixed source archive roots")
            path = "/".join(parts[1:])
            if member.isdir() or path not in expected:
                continue
            if path in files or not (member.isfile() or member.issym()):
                raise ValueError("Duplicate or unsupported runtime entry")
            entry = expected[path]
            if member.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), member.linkname))
                if member.linkname.startswith("/") or target == ".." or target.startswith("../") or entry["mode"] != "120000":
                    raise ValueError("Unsafe runtime symlink")
                data = member.linkname.encode()
            else:
                if entry["mode"] not in {"100644", "100755"}:
                    raise ValueError("Unexpected runtime file mode")
                data = archive.extractfile(member).read()
            if member.isfile() and git_blob(data) != entry["sha"]:
                # GitHub source archives can apply declared Windows CRLF checkout
                # rules. Admit canonical LF bytes only when the Git blob proves them.
                normalized = data.replace(b"\r\n", b"\n")
                if git_blob(normalized) == entry["sha"]:
                    data = normalized
            if git_blob(data) != entry["sha"]:
                raise ValueError("Runtime source blob integrity mismatch: " + path)
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError("Potential credential marker requires review: " + path)
            files[path] = (data, entry["mode"])
    if files.keys() != expected.keys():
        raise ValueError("Runtime archive coverage is incomplete")
    inventory = [{"path": path, "sourceBlob": expected[path]["sha"], "sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
                 for path, (data, mode) in sorted(files.items())]
    manifest = {"schemaVersion": 1, "product": "Mithril Agent", "sourceRevision": revision,
                "sourceTree": source_tree["sha"], "publicationState": "candidate",
                "license": "MIT", "runtimeFiles": inventory}
    if controller_root or controller_revision:
        if not controller_root or not re.fullmatch(r"[a-f0-9]{40}", controller_revision or ""):
            raise ValueError("The session controller requires its immutable source revision")
        def git(*args):
            return subprocess.run(["git", "-C", str(controller_root), *args], check=True, capture_output=True).stdout
        origin = git("remote", "get-url", "origin").decode().strip()
        if origin not in {"git@github.com:mithril-lang/mithril-fund.git", "https://github.com/mithril-lang/mithril-fund.git"}:
            raise ValueError("Unexpected session controller source repository")
        entries = []
        for name in ("session.py", "backend.py"):
            source = "scripts/agent-runtime/" + name
            blob = git("rev-parse", controller_revision + ":" + source).decode().strip()
            data = git("show", controller_revision + ":" + source)
            if git_blob(data) != blob or any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError("Session controller integrity requires review")
            target = "scripts/mithril-runtime/" + name
            files[target] = (data, "100644")
            entries.append({"path": target, "sourcePath": source, "sourceBlob": blob, "sha256": hashlib.sha256(data).hexdigest()})
        manifest["sessionController"] = {"repository": "mithril-lang/mithril-fund", "sourceRevision": controller_revision, "files": entries}
    files[".mithril-runtime-provenance.json"] = ((json.dumps(manifest, indent=2) + "\n").encode(), "100644")
    readme = """# Mithril Agent runtime

The MIT-licensed runtime used by the Mithril CLI and Mithril Desktop, built on
Hermes Agent by Nous Research. Configuration and conversations use `~/.mithril`.

This is a candidate runtime source snapshot for installation qualification.
Public installer availability is announced at https://console.mithril.fund/agent.
The runtime provenance manifest binds each original file to its source revision.

Upstream project: https://github.com/NousResearch/hermes-agent
License: [MIT](LICENSE).
"""
    files["README.md"] = (readme.encode(), "100644")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing export")
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise ValueError("An unfinished export already exists")
    with temporary.open("xb") as stream:
        with gzip.GzipFile(fileobj=stream, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as output:
                for path, (data, mode) in sorted(files.items()):
                    member = tarfile.TarInfo(path)
                    member.mtime = 0
                    member.mode = 0o755 if mode == "100755" else 0o644
                    if mode == "120000":
                        member.type = tarfile.SYMTYPE
                        member.linkname = data.decode()
                        output.addfile(member)
                    else:
                        member.size = len(data)
                        output.addfile(member, io.BytesIO(data))
    temporary.rename(destination)
    return {"sourceRevision": revision, "runtimeFiles": len(inventory),
            "runtimeBytes": sum(len(data) for data, _ in files.values()),
            "archiveSha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-docker", action="store_true")
    parser.add_argument("--controller-root")
    parser.add_argument("--controller-revision")
    options = parser.parse_args()
    result = export_runtime(options.source_archive, json.loads(Path(options.source_tree).read_text()), options.revision, options.output, options.include_docker, options.controller_root, options.controller_revision)
    print(json.dumps(result))
