#!/usr/bin/python3
"""Mithril-owned WireGuard control through NetworkManager. JSON stdin/stdout only."""
import base64
import datetime as dt
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile

NMCLI = "/usr/bin/nmcli"
ID = re.compile(r"[a-f0-9]{32}")
FIELDS = {"version", "id", "name", "region", "egressMode", "expectedEgressIPv4", "expiresAt",
          "transport", "wireguardConfig"}

def expiry(text):
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Profile expiry requires a timezone")
    return value.astimezone(dt.timezone.utc)

def parse_profile(raw, fresh=True):
    if len(raw) > 32768:
        raise ValueError("Invalid profile")
    p = json.loads(raw)
    if not isinstance(p, dict) or set(p) != FIELDS or type(p["version"]) is not int or p["version"] != 1:
        raise ValueError("Invalid profile")
    if not all(isinstance(p[k], str) for k in FIELDS - {"version"}):
        raise ValueError("Invalid profile")
    if not ID.fullmatch(p["id"]) or not 1 <= len(p["name"]) <= 80:
        raise ValueError("Invalid profile")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", p["region"]) or p["egressMode"] not in {"shared", "dedicated"}:
        raise ValueError("Invalid profile")
    ipaddress.IPv4Address(p["expectedEgressIPv4"])
    expires = expiry(p["expiresAt"])
    if fresh and expires <= dt.datetime.now(dt.timezone.utc):
        raise ValueError("Expired profile")
    if p["transport"] != "wireguard":
        raise ValueError("WireGuard profile required")
    values, counts, section = {}, {"Interface": 0, "Peer": 0}, ""
    allowed = {"Interface": {"PrivateKey", "Address", "DNS"},
               "Peer": {"PublicKey", "Endpoint", "AllowedIPs", "PersistentKeepalive"}}
    for raw_line in p["wireguardConfig"].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in {"[Interface]", "[Peer]"}:
            section = line[1:-1]
            counts[section] += 1
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        name = section + "." + key
        if not sep or key not in allowed.get(section, set()) or name in values:
            raise ValueError("Unsupported configuration")
        values[name] = value
    if counts != {"Interface": 1, "Peer": 1} or not {
        "Interface.PrivateKey", "Interface.Address", "Interface.DNS",
        "Peer.PublicKey", "Peer.Endpoint", "Peer.AllowedIPs"
    }.issubset(values):
        raise ValueError("Incomplete configuration")
    for key in ["Interface.PrivateKey", "Peer.PublicKey"]:
        if len(base64.b64decode(values[key], validate=True)) != 32:
            raise ValueError("Invalid key")
    address = ipaddress.IPv4Interface(values["Interface.Address"])
    if address.network.prefixlen != 32:
        raise ValueError("One IPv4 tunnel address required")
    ipaddress.IPv4Address(values["Interface.DNS"])
    endpoint, port = values["Peer.Endpoint"].rsplit(":", 1)
    ipaddress.IPv4Address(endpoint)
    if not port.isdecimal() or not 1024 <= int(port) <= 65535:
        raise ValueError("Invalid endpoint")
    if set(map(str.strip, values["Peer.AllowedIPs"].split(","))) != {"0.0.0.0/0", "::/0"}:
        raise ValueError("Full IPv4 and IPv6 routes required")
    if "Peer.PersistentKeepalive" in values and not 0 <= int(values["Peer.PersistentKeepalive"]) <= 65535:
        raise ValueError("Invalid keepalive")
    p["_dns"] = values["Interface.DNS"]
    return p

def metadata(p):
    result = {k: p[k] for k in ["id", "name", "region", "egressMode", "expectedEgressIPv4", "expiresAt"]}
    if p.get("_wireguardPublicKey"):
        result["wireguardPublicKey"] = p["_wireguardPublicKey"]
    return result

def nm(*args, source=None):
    # Never request --show-secrets or put a private key/password in arguments.
    r = subprocess.run([NMCLI, "--wait", "20", *args], input=source, capture_output=True, text=True,
                       timeout=25, env={**os.environ, "LC_ALL": "C"})
    if r.returncode:
        raise RuntimeError("NetworkManager operation failed")
    if len(r.stdout.encode()) > 65536:
        raise RuntimeError("Invalid NetworkManager response")
    return r.stdout.strip()

def private_root():
    root = Path.home() / ".local/share/mithril/vpn"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Private storage unavailable")
    os.chmod(root, 0o700)
    return root

def owned(saved):
    uuid = saved["nmUuid"]
    if not re.fullmatch(r"[a-f0-9-]{36}", uuid):
        raise RuntimeError("Invalid native ownership")
    data = nm("--get-values", "connection.id,connection.uuid,connection.type", "connection", "show", "uuid", uuid).splitlines()
    if data != ["mithril-vpn-" + saved["profile"]["id"], uuid, "wireguard"]:
        raise RuntimeError("Native profile ownership changed")
    return uuid

def read_saved(root):
    path = root / "metadata.json"
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        if os.fstat(handle.fileno()).st_size > 4096:
            raise RuntimeError("Invalid metadata")
        return json.load(handle)

def save(root, value):
    fd, path = tempfile.mkstemp(prefix="metadata-", dir=root)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(path, root / "metadata.json")
    finally:
        if os.path.exists(path):
            os.unlink(path)

def active(saved):
    if not saved:
        return False
    uuid = owned(saved)
    return uuid in nm("--get-values", "UUID", "connection", "show", "--active").splitlines()

def snapshot(saved, reason=None, unavailable=False):
    state = "unavailable" if unavailable else "connected" if active(saved) else "disconnected"
    return {"version": 1, "platform": "linux", "state": state,
            "profiles": [saved["profile"]] if saved else [],
            "activeProfileId": saved["profile"]["id"] if saved and state == "connected" else None,
            "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "egressVerification": "unverified",
            "reason": reason or ("native-component-missing" if unavailable else "profile-required" if not saved else None)}

def execute(request):
    root = private_root()
    lock = os.open(root / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = read_saved(root)
        if not Path(NMCLI).is_file() or not Path("/usr/bin/wg").is_file():
            return snapshot(saved, unavailable=True)
        version = re.search(r"(\d+)\.(\d+)", nm("--version"))
        if not version or tuple(map(int, version.groups())) < (1, 46):
            return snapshot(saved, unavailable=True)
        nm("--get-values", "STATE", "general", "status")
        op = request.get("operation")
        if op == "getState":
            if saved and expiry(saved["profile"]["expiresAt"]) <= dt.datetime.now(dt.timezone.utc) and active(saved):
                nm("connection", "down", "uuid", owned(saved))
                return snapshot(saved, "profile-expired")
            return snapshot(saved)
        if op == "disconnect":
            if saved and active(saved):
                nm("connection", "down", "uuid", owned(saved))
            return snapshot(saved)
        if op in {"connect", "forgetProfile"}:
            if not saved or request.get("profileId") != saved["profile"]["id"]:
                raise ValueError("Invalid profile ID")
            uuid = owned(saved)
            if op == "connect":
                if expiry(saved["profile"]["expiresAt"]) <= dt.datetime.now(dt.timezone.utc):
                    return snapshot(saved, "profile-expired")
                # NetworkManager owns the private key and full-tunnel routing.
                nm("connection", "up", "uuid", uuid)
                return snapshot(saved)
            if active(saved):
                raise RuntimeError("Disconnect before removing")
            nm("connection", "delete", "uuid", uuid)
            (root / "metadata.json").unlink()
            return snapshot(None)
        if op != "importProfile" or active(saved):
            raise ValueError("Invalid operation")
        path = Path(request["importPath"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(32769)
        value = json.loads(raw)
        config = value.get("wireguardConfig", "")
        if "GENERATE_ON_DEVICE" in config:
            pattern = r"(?m)^[ \t]*PrivateKey[ \t]*=[ \t]*GENERATE_ON_DEVICE[ \t\r]*$"
            if config.count("GENERATE_ON_DEVICE") != 1 or not re.search(pattern, config):
                raise ValueError("Invalid key template")
            generated = subprocess.run(["/usr/bin/wg", "genkey"], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
            value["wireguardConfig"] = re.sub(pattern, "PrivateKey = " + generated, config, count=1)
        p = parse_profile(json.dumps(value).encode())
        key = next(line.partition("=")[2].strip() for line in p["wireguardConfig"].splitlines() if line.strip().startswith("PrivateKey"))
        public = subprocess.run(["/usr/bin/wg", "pubkey"], input=key, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        if len(base64.b64decode(public, validate=True)) != 32:
            raise ValueError("Invalid public key")
        p["_wireguardPublicKey"] = public
        username = pwd.getpwuid(os.getuid()).pw_name
        temporary_uuid = None
        with tempfile.TemporaryDirectory(prefix="import-", dir=root) as folder:
            token = "mwg" + secrets.token_hex(6)
            conf = Path(folder) / (token + ".conf")
            conf.write_text(p["wireguardConfig"])
            conf.chmod(0o600)
            try:
                # Import in memory first: no transient auto-connect or persistent
                # unrestricted profile may exist before ownership is applied.
                nm("connection", "import", "--temporary", "type", "wireguard", "file", str(conf))
                temporary_uuid = nm("--get-values", "connection.uuid", "connection", "show", token)
                if not re.fullmatch(r"[a-f0-9-]{36}", temporary_uuid):
                    raise RuntimeError("Invalid native profile")
                ipv6 = "fd42:6d69:7468:7269:" + ":".join(p["id"][i:i+4] for i in range(16, 32, 4)) + "/128"
                nm("connection", "modify", "uuid", temporary_uuid,
                   "connection.id", "mithril-vpn-" + p["id"], "connection.autoconnect", "no",
                   "connection.permissions", "user:" + username,
                   "wireguard.ip4-auto-default-route", "yes", "wireguard.ip6-auto-default-route", "yes",
                   "ipv4.never-default", "no", "ipv4.dns", p["_dns"], "ipv4.dns-priority", "-100",
                   "ipv6.method", "manual", "ipv6.addresses", ipv6, "ipv6.never-default", "no",
                   "ipv6.dns-priority", "-100")
                new = {"profile": metadata(p), "nmUuid": temporary_uuid}
                owned(new)
                # Explicitly persist a profile initially imported in memory.
                # Modify retains the original in-memory persistence mode.
                nm("connection", "edit", "uuid", temporary_uuid, source="save persistent\nquit\n")
                nm("connection", "reload")
                owned(new)
                if saved:
                    nm("connection", "delete", "uuid", owned(saved))
                save(root, new)
                temporary_uuid = None
                return snapshot(new)
            finally:
                if temporary_uuid:
                    try:
                        nm("connection", "delete", "uuid", temporary_uuid)
                    except Exception:
                        pass
    finally:
        os.close(lock)

def main():
    raw = sys.stdin.buffer.read(4097)
    try:
        request = json.loads(raw)
        if len(raw) > 4096 or not isinstance(request, dict) or set(request) - {"operation", "profileId", "importPath"}:
            raise ValueError("Invalid request")
        result = execute(request)
    except Exception:
        # Never expose paths, imported configuration or framework error text.
        result = {"version": 1, "platform": "linux", "state": "unknown", "profiles": [],
                  "activeProfileId": None, "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "egressVerification": "unverified", "reason": "operation-failed"}
    print(json.dumps(result))

if __name__ == "__main__":
    main()
