"""Verify source snapshot bytes and the runtime-owned override layer."""

import hashlib
import json
import re
import subprocess
from pathlib import Path


def compare_git_blobs(entries):
    proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for ref, expected, label in entries:
            proc.stdin.write(f"{ref}\n".encode())
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if expected is None:
                if len(header) == 2 and header[1] == b"missing":
                    continue
                raise SystemExit(f"expected no base file for {label}")
            if len(header) != 3 or header[1] != b"blob":
                raise SystemExit(f"committed file is missing: {label}")
            size = int(header[2])
            data = proc.stdout.read(size)
            if proc.stdout.read(1) != b"\n":
                raise SystemExit(f"could not read committed file: {label}")
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise SystemExit(f"provenance mismatch: {label}")
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code:
        raise SystemExit(f"git cat-file failed with status {code}")


manifest = json.loads(Path(".mithril-runtime-provenance.json").read_text(encoding="utf-8"))
if manifest.get("schemaVersion") != 2:
    raise SystemExit("runtime provenance manifest must use schema version 2")
if manifest.get("product") != "Mithril Agent" or manifest.get("publicationState") != "candidate":
    raise SystemExit("runtime product or publication state is invalid")
if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("sourceRevision", "")):
    raise SystemExit("runtime source revision is not immutable")
files = manifest.get("runtimeFiles")
override_layer = manifest.get("runtimeOverrides")
if not isinstance(files, list) or not files or not isinstance(override_layer, dict):
    raise SystemExit("runtime provenance inventory is incomplete")
base_revision = override_layer.get("baseRuntimeRevision", "")
if not re.fullmatch(r"[0-9a-f]{40}", base_revision):
    raise SystemExit("runtime override base revision is not immutable")
overrides = override_layer.get("files")
if not isinstance(overrides, list):
    raise SystemExit("runtime override inventory is missing")
override_map = {entry["path"]: entry for entry in overrides}
if len(override_map) != len(overrides):
    raise SystemExit("runtime override inventory contains duplicate paths")
canonical = json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode()
if hashlib.sha256(canonical).hexdigest() != override_layer.get("sha256"):
    raise SystemExit("runtime override inventory digest is invalid")

base_checks = []
head_checks = []
known_paths = set()
for item in files:
    path = Path(item["path"])
    if path.is_absolute() or ".." in path.parts or not path.is_file():
        raise SystemExit(f"runtime file missing or unsafe: {path}")
    name = path.as_posix()
    known_paths.add(name)
    override = override_map.get(name)
    expected = override["sha256"] if override else item["sha256"]
    head_checks.append((f"HEAD:{name}", expected, name))
    if override:
        base_checks.append((f"{base_revision}:{name}", override["baseSha256"], name))
for name, override in override_map.items():
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.is_file():
        raise SystemExit(f"runtime override missing or unsafe: {path}")
    if name not in known_paths:
        head_checks.append((f"HEAD:{name}", override["sha256"], name))
        base_checks.append((f"{base_revision}:{name}", override["baseSha256"], name))

compare_git_blobs(head_checks)
compare_git_blobs(base_checks)
print(f"Verified {len(files)} source files and {len(overrides)} runtime overrides from {manifest['sourceRevision']}.")
