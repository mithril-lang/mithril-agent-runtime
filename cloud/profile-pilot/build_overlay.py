"""Produce the immutable public code overlay; never reads profile or credential data."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def build():
    paths = ["hermes_cli/profile_placement.py", "cloud/profile-pilot/runtime.py", "cloud/profile-pilot/install.py", "cloud/profile-pilot/worker.mjs"]
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    source = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    files = {p: base64.b64encode((ROOT / p).read_bytes()).decode() for p in paths[:2]}
    return {"source": source, "hashes": hashes, "files": files, "installer": base64.b64encode((HERE / "install.py").read_bytes()).decode()}


if __name__ == "__main__":
    (HERE / "overlay.mjs").write_text("export default " + json.dumps(build(), separators=(",", ":")) + ";\n")
