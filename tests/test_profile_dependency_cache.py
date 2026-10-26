"""Dependency selection must stay current without parsing every unchanged Profile."""

import json
import os

import pytest

from pm import plugins_state


def _home(root, name, config):
    home = root if name is None else root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(config, encoding="utf-8")
    return home


def test_cache_reuses_exact_bytes_and_tracks_live_profiles(tmp_path, monkeypatch):
    root = _home(tmp_path / "home", None, "plugins:\n  enabled: [base]\nsecret: private-value\n")
    other = _home(root, "other", "plugins:\n  enabled: [extra]\nmemory:\n  provider: memory\n")
    for home, names in ((root, ("base",)), (other, ("extra", "other", "memory"))):
        for name in names:
            (home / "plugins" / name).mkdir(parents=True)
    homes = [root, other]
    cache = tmp_path / "selection-cache.json"
    monkeypatch.setattr(plugins_state, "dependency_homes", lambda: list(homes))
    monkeypatch.setattr(plugins_state, "_selection_cache_path", lambda: cache)
    monkeypatch.setattr(plugins_state, "_selection_cache_identity", lambda: "parser-v1")
    original_parse = plugins_state._parse_home_selection
    parsed = []

    def count_parse(home, data):
        parsed.append(home)
        return original_parse(home, data)

    monkeypatch.setattr(plugins_state, "_parse_home_selection", count_parse)
    expected = {root / "plugins": ["base"], other / "plugins": ["extra", "memory"]}
    assert plugins_state.enabled_plugins_ordered() == expected
    assert parsed == homes
    assert plugins_state.enabled_plugins_ordered() == expected
    assert parsed == homes
    assert "private-value" not in cache.read_text()

    monkeypatch.setattr(plugins_state, "_selection_cache_identity", lambda: "parser-v2")
    assert plugins_state.enabled_plugins_ordered() == expected
    assert parsed == homes + homes

    config = other / "config.yaml"
    before = config.stat()
    config.write_text("plugins:\n  enabled: [other]\nmemory:\n  provider: memory\n", encoding="utf-8")
    assert config.stat().st_size == before.st_size
    os.utime(config, ns=(before.st_atime_ns, before.st_mtime_ns))
    expected[other / "plugins"] = ["other", "memory"]
    assert plugins_state.enabled_plugins_ordered() == expected
    assert parsed == [root, other, root, other, other]

    homes.remove(other)
    assert plugins_state.enabled_plugins_ordered() == {root / "plugins": ["base"]}
    assert set(json.loads(cache.read_text())["entries"]) == {str(root)}


def test_cache_never_hides_invalid_config_or_corruption(tmp_path, monkeypatch):
    root = _home(tmp_path / "home", None, "plugins:\n  enabled: [base]\n")
    other = _home(root, "other", "plugins:\n  enabled: [extra]\n")
    for home, name in ((root, "base"), (other, "extra")):
        (home / "plugins" / name).mkdir(parents=True)
    cache = tmp_path / "selection-cache.json"
    monkeypatch.setattr(plugins_state, "dependency_homes", lambda: [root, other])
    monkeypatch.setattr(plugins_state, "_selection_cache_path", lambda: cache)
    monkeypatch.setattr(plugins_state, "_selection_cache_identity", lambda: "parser-v1")
    assert plugins_state.enabled_plugins_ordered() == {
        root / "plugins": ["base"], other / "plugins": ["extra"]}

    (other / "config.yaml").write_text("plugins: [", encoding="utf-8")
    assert plugins_state.enabled_plugins_ordered(skip_invalid_secondary=True) == {
        root / "plugins": ["base"]}
    with pytest.raises(ValueError, match="could not parse plugin selection"):
        plugins_state.enabled_plugins_ordered()

    (root / "config.yaml").write_text("plugins: [", encoding="utf-8")
    with pytest.raises(ValueError, match="could not parse plugin selection"):
        plugins_state.enabled_plugins_ordered(skip_invalid_secondary=True)

    (root / "config.yaml").write_text("plugins:\n  enabled: [base]\n", encoding="utf-8")
    (other / "config.yaml").write_text("plugins:\n  enabled: [extra]\n", encoding="utf-8")
    cache.write_text("{broken", encoding="utf-8")
    assert plugins_state.enabled_plugins_ordered() == {
        root / "plugins": ["base"], other / "plugins": ["extra"]}
