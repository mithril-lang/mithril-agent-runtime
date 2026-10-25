"""The post-update skill pass shares source work without sharing profile state."""

from hermes_cli import update_cmd_maint
from tools import skills_sync


def test_update_sync_keeps_profile_edits_deletions_and_opt_out(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    (root / "config.yaml").write_text("# default profile\n")
    homes = {name: root / "profiles" / name
             for name in ("edited", "deleted", "external", "opted-out")}
    for home in homes.values():
        home.mkdir(parents=True)
        (home / "profile.yaml").write_text("# named profile\n")
    (homes["opted-out"] / ".no-bundled-skills").touch()
    external = tmp_path / "external" / "example"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("---\nname: example\n---\nexternal copy\n")
    (homes["external"] / "config.yaml").write_text(
        f"skills:\n  external_dirs:\n    - {external.parent}\n")

    bundled = tmp_path / "bundled"
    skill = bundled / "research" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: example\n---\nversion one\n")
    optional = tmp_path / "optional"
    optional.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_BUNDLED_SKILLS", str(bundled))
    monkeypatch.setenv("HERMES_OPTIONAL_SKILLS", str(optional))

    original_hash = skills_sync._dir_hash
    source_hashes = []

    def count_source_hashes(path, **kwargs):
        if path == skill.parent:
            source_hashes.append(path)
        return original_hash(path, **kwargs)

    monkeypatch.setattr(skills_sync, "_dir_hash", count_source_hashes)
    update_cmd_maint._sync_profiles_after_update()
    assert len(source_hashes) == 1
    local = lambda home: home / "skills" / "research" / "example" / "SKILL.md"
    assert local(root).read_text() == skill.read_text()
    assert local(homes["edited"]).read_text() == skill.read_text()
    assert local(homes["deleted"]).read_text() == skill.read_text()
    assert not local(homes["external"]).exists()
    assert not local(homes["opted-out"]).exists()

    local(homes["edited"]).write_text("user's version\n")
    local(homes["deleted"]).unlink()
    skill.write_text("---\nname: example\n---\nversion two\n")
    update_cmd_maint._sync_profiles_after_update()
    assert len(source_hashes) == 2
    assert "version two" in local(root).read_text()
    assert local(homes["edited"]).read_text() == "user's version\n"
    assert not local(homes["deleted"]).exists()
    assert not local(homes["external"]).exists()
    assert not local(homes["opted-out"]).exists()
    assert skills_sync._skills_dir() == root / "skills"

    update_cmd_maint._sync_profiles_after_update()  # A → B → A keeps the default scope.
    assert "version two" in local(root).read_text()
    assert local(homes["edited"]).read_text() == "user's version\n"
