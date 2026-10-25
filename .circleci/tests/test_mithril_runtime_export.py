"""Exercise the public exporter through its real CLI and Git blob protocol."""

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


EXPORTER = Path(__file__).resolve().parents[2] / "scripts/export-mithril-runtime.py"
REQUIRED = {
    "LICENSE", "pyproject.toml", "uv.lock", "hermes_bootstrap.py", "run_agent.py",
    "scripts/install.sh", "scripts/install.ps1", "hermes_cli/main.py",
    "hermes_cli/skin_engine.py", "hermes_cli/data/mithril-skin.json", "hermes_cli/mithril_auth.py",
}


def make_source(tmp_path, secret=False, traversal=False, changed=False, docker=False):
    files = {name: b"fixture runtime input\n" for name in REQUIRED}
    files[".env"] = b"private operator configuration\n"
    files["docs/private.md"] = b"private source notes\n"
    files["docs/operations/mithril-account-login.md"] = b"public sign-in and update policy\n"
    files[".github/owner.json"] = b"private owner state\n"
    files[".circleci/config.yml"] = b"private CI configuration\n"
    if docker:
        for name in {"Dockerfile", "docker/build_agent.py", "docker/hermes-exec-shim.sh", "docker/entrypoint-dispatch.sh"}:
            files[name] = b"verified container input\n"
    if secret:
        files["leaked.py"] = ("ghp_" + "x" * 40).encode()
    entries = []
    for name, data in files.items():
        result = subprocess.run(["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True, cwd=tmp_path)
        entries.append({"path": name, "type": "blob", "mode": "100644", "sha": result.stdout.decode().strip()})
    tree = tmp_path / "tree.json"
    tree.write_text(json.dumps({"sha": "e" * 40, "truncated": False, "tree": entries}))
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name, data in files.items():
            if name == "scripts/install.ps1":
                data = data.replace(b"\n", b"\r\n")
            if changed and name == "run_agent.py":
                data = b"unverified replacement\n"
            member = tarfile.TarInfo("source-prefix/" + name)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
        if traversal:
            member = tarfile.TarInfo("source-prefix/../escape")
            member.size = 1
            output.addfile(member, io.BytesIO(b"x"))
    return archive, tree


def export(tmp_path, source, name="runtime.tar.gz", extra=()):
    output = tmp_path / name
    result = subprocess.run([
        sys.executable, str(EXPORTER), "--source-archive", str(source[0]),
        "--source-tree", str(source[1]), "--revision", "a" * 40, "--output", str(output), *extra,
    ], capture_output=True, text=True, cwd=tmp_path)
    return result, output


def test_container_export_requires_verified_build_inputs_and_preserves_operator_exclusions(tmp_path):
    missing, output = export(tmp_path, make_source(tmp_path), extra=("--include-docker",))
    assert missing.returncode != 0 and not output.exists()
    result, output = export(tmp_path, make_source(tmp_path, docker=True), extra=("--include-docker",))
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        names = set(archive.getnames())
        assert {"docker/build_agent.py", "docker/hermes-exec-shim.sh", "docker/entrypoint-dispatch.sh"} <= names
        assert not names & {".env", ".github/owner.json", "docs/private.md"}


def test_controller_overlay_exports_only_the_pinned_fund_helpers_with_provenance(tmp_path):
    controller = tmp_path / "controller"
    controller.mkdir()
    subprocess.run(["git", "init", "-q", str(controller)], check=True)
    subprocess.run(["git", "-C", str(controller), "remote", "add", "origin", "https://github.com/mithril-lang/mithril-fund.git"], check=True)
    files = controller / "scripts/agent-runtime"
    files.mkdir(parents=True)
    for name in ("session.py", "backend.py"):
        (files / name).write_text("print('verified helper')\n")
    (controller / "private.txt").write_text("not a runtime input\n")
    subprocess.run(["git", "-C", str(controller), "add", "."], check=True)
    subprocess.run(["git", "-C", str(controller), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture inputs"], check=True)
    revision = subprocess.check_output(["git", "-C", str(controller), "rev-parse", "HEAD"], text=True).strip()
    (files / "session.py").write_text("uncommitted replacement\n")
    result, output = export(tmp_path, make_source(tmp_path), extra=("--controller-root", str(controller), "--controller-revision", revision))
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert archive.extractfile("scripts/mithril-runtime/session.py").read() == b"print('verified helper')\n"
        assert "private.txt" not in archive.getnames()
        manifest = json.load(archive.extractfile(".mithril-runtime-provenance.json"))
        assert manifest["sessionController"]["sourceRevision"] == revision
        assert len(manifest["sessionController"]["files"]) == 2


def test_public_export_preserves_verified_runtime_and_excludes_operator_files(tmp_path):
    source = make_source(tmp_path)
    first, one = export(tmp_path, source)
    second, two = export(tmp_path, source, "repeat.tar.gz")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert one.read_bytes() == two.read_bytes()
    with tarfile.open(one) as archive:
        names = set(archive.getnames())
        assert REQUIRED <= names
        assert not names & {".env", "docs/private.md", ".github/owner.json", ".circleci/config.yml"}
        assert "docs/operations/mithril-account-login.md" in names
        readme = archive.extractfile("README.md").read().decode()
        assert "device approval flow" in readme
        assert "updates.auto_apply: false" in readme
        assert archive.extractfile("scripts/install.ps1").read() == b"fixture runtime input\n"
        manifest = json.load(archive.extractfile(".mithril-runtime-provenance.json"))
        assert manifest["publicationState"] == "candidate"
        assert {item["path"] for item in manifest["runtimeFiles"]} == REQUIRED | {
            "docs/operations/mithril-account-login.md"
        }


@pytest.mark.parametrize("change", [{"secret": True}, {"traversal": True}, {"changed": True}])
def test_public_export_fails_closed_before_public_output(tmp_path, change):
    result, output = export(tmp_path, make_source(tmp_path, **change))
    assert result.returncode != 0
    assert not output.exists()
    assert "ghp_" + "x" * 40 not in result.stderr


def test_public_export_refuses_incomplete_source_and_destination_overwrite(tmp_path):
    source = make_source(tmp_path)
    tree = json.loads(source[1].read_text())
    tree["truncated"] = True
    source[1].write_text(json.dumps(tree))
    result, output = export(tmp_path, source)
    assert result.returncode != 0 and not output.exists()
    source = make_source(tmp_path)
    first, output = export(tmp_path, source)
    assert first.returncode == 0
    original = output.read_bytes()
    repeated, _ = export(tmp_path, source)
    assert repeated.returncode != 0
    assert output.read_bytes() == original
