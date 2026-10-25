"""Public, content-addressed copies of reviewed binary inputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from pm.downloader import Source
from pm.index_config import npm_registry_url
from urllib.parse import urlsplit

_LAYOUT = json.loads(Path(__file__).with_name("artifact-mirror.json").read_text(encoding="utf-8-sig"))
KEY_PREFIX = _LAYOUT["prefix"]
PUBLIC_PREFIX = _LAYOUT["origin"] + "/" + KEY_PREFIX


def object_key(sha256: str) -> str:
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("A pinned input requires a full lowercase SHA256")
    return KEY_PREFIX + sha256


def mirror_url(sha256: str) -> str:
    object_key(sha256)
    return PUBLIC_PREFIX + sha256


def pinned_source(url: str, dest: Path, sha256: str) -> Source:
    if os.environ.get("MITHRIL_AGENT") == "1" or os.environ.get("MITHRIL_AGENT_CLI_LAUNCHER") == "1":
        host = (urlsplit(url).hostname or "").lower()
        if host == "nousresearch.com" or host.endswith(".nousresearch.com"):
            raise ValueError("Mithril dependencies require their pinned vendor source")
        return Source(npm_registry_url(url, os.environ), dest, sha256, fallbacks=())
    archive = mirror_url(sha256)
    # The lock records registry.npmjs.org; a user's npm mirror serves the same pinned bytes (#123132).
    url = npm_registry_url(url, os.environ)
    return Source(url, dest, sha256, fallbacks=() if url == archive else (archive,))
