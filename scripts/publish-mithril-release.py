"""Publish only verified signed CLI assets through the original runtime CI owner."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_cli import mithril_updates as updates
REPO = "mithril-lang/mithril-agent-runtime"
REPO_ID = 1418374467
PREFIX = "/repos/" + REPO
OUTPUT = Path("/tmp/mithril-cli-publication")
NAMES = {"install-mithril.sh", "mithril-cli-runtime.tar.gz", "mithril-update.json", "release-evidence.json", "SHA256SUMS"}
class Held(Exception):
    pass
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
def release_token(env=os.environ, opener=None):
    # A dedicated already-scoped token may be projected by the credential owner.
    # Normal GitHub App operation mints a new repo-limited token in each cloud job.
    if env.get("MITHRIL_CLI_RELEASE_TOKEN"):
        return env["MITHRIL_CLI_RELEASE_TOKEN"]
    app_id, installation_id = env.get("MITHRIL_CLI_RELEASE_APP_ID", ""), env.get("MITHRIL_CLI_RELEASE_INSTALLATION_ID", "")
    pem = env.get("MITHRIL_CLI_RELEASE_APP_KEY", "")
    if not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (app_id, installation_id)) or not pem:
        raise Held("scoped_release_credential_missing")
    try:
        import base64
        import time
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=")
        now = int(time.time())
        message = encode(canonical({"alg":"RS256","typ":"JWT"})) + b"." + encode(canonical({"iat":now-30,"exp":now+300,"iss":app_id}))
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        jwt = (message+b"."+encode(key.sign(message,padding.PKCS1v15(),hashes.SHA256()))).decode()
        request = urllib.request.Request("https://api.github.com/app/installations/"+installation_id+"/access_tokens",
            data=canonical({"repository_ids":[REPO_ID],"permissions":{"contents":"write"}}),method="POST",
            headers={"Authorization":"Bearer "+jwt,"Accept":"application/vnd.github+json","Content-Type":"application/json",
                     "X-GitHub-Api-Version":"2026-03-10","User-Agent":"Mithril-CLI-Publisher"})
        client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with client.open(request,timeout=20) as response:
            raw = response.read(1024*1024+1)
            if len(raw)>1024*1024:raise Held("credential_response_budget")
        value=json.loads(raw)
        repos=value.get("repositories",[])
        if (value.get("permissions",{}).get("contents")!="write" or len(repos)!=1 or repos[0].get("id")!=REPO_ID
            or repos[0].get("full_name")!=REPO or not isinstance(value.get("token"),str) or not value["token"]):
            raise Held("minted_credential_scope_mismatch")
        return value["token"]
    except Held:
        raise
    except Exception:
        raise Held("scoped_credential_mint_failed") from None

def scoped_authority(repo, installation):
    rows = installation.get("repositories", [])
    if (repo.get("id") != REPO_ID or repo.get("full_name") != REPO or repo.get("permissions", {}).get("push") is not True
        or installation.get("total_count") != 1 or len(rows) != 1
        or rows[0].get("id") != REPO_ID or rows[0].get("full_name") != REPO):
        raise Held("repository_scoped_write_authority_required")
def git(*args):
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE, timeout=30)
    except Exception:
        raise Held("source_read_failed") from None
def identity():
    sha = os.environ.get("CIRCLE_SHA1", "")
    if (os.environ.get("CIRCLECI") != "true" or os.environ.get("CIRCLE_BRANCH") != "main"
        or os.environ.get("CIRCLE_PROJECT_USERNAME") != "mithril-lang"
        or os.environ.get("CIRCLE_PROJECT_REPONAME") != "mithril-agent-runtime"
        or not re.fullmatch(r"[a-f0-9]{40}", sha)
        or git("rev-parse", "HEAD").decode().strip() != sha or git("status", "--porcelain").strip()
        or git("ls-remote", "origin", "refs/heads/main").decode().split()[0] != sha):
        raise Held("exact_original_main_required")
    for key in ("CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_WORKFLOW_JOB_ID"):
        if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", os.environ.get(key, "")):
            raise Held("original_job_identity_required")
    return sha
def verified_assets(folder, sha, workflow, version, sequence):
    if folder.is_symlink() or not folder.is_dir() or {p.name for p in folder.iterdir()} != NAMES:
        raise Held("release_inventory_mismatch")
    data = {}
    for p in folder.iterdir():
        if p.is_symlink() or not p.is_file() or not 0 < p.stat().st_size <= (256*1024*1024 if p.name.endswith(".tar.gz") else 1024*1024):
            raise Held("release_asset_budget")
        data[p.name] = p.read_bytes()
    inventory = {name: {"size": len(raw), "sha256": digest(raw)} for name, raw in sorted(data.items())}
    rows = {}
    for line in data["SHA256SUMS"].decode("ascii").splitlines():
        m = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not m or m[2] in rows:
            raise Held("release_checksums_invalid")
        rows[m[2]] = m[1]
    if rows != {name: item["sha256"] for name, item in inventory.items() if name != "SHA256SUMS"}:
        raise Held("release_checksums_mismatch")
    envelope = json.loads(data["mithril-update.json"])
    trust = updates.load_trust()
    payload = updates.verify(envelope, trust)
    public_key = base64.b64decode(trust["keys"][envelope["key_id"]]["public_key"], validate=True)
    evidence = json.loads(data["release-evidence.json"])
    if (payload["runtime_revision"] != sha or payload["version"] != version or payload["sequence"] != sequence
        or evidence.get("runtime_revision") != sha or evidence.get("version") != version
        or evidence.get("schema") != 1 or evidence.get("key_id") != envelope["key_id"]
        or evidence.get("public_key_sha256") != digest(public_key)
        or evidence.get("provider") != "circleci" or evidence.get("workflow_id") != workflow
        or evidence.get("pipeline_id") != os.environ.get("CIRCLE_PIPELINE_ID")
        or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", evidence.get("job_id", ""))
        or payload["provenance"]["manifest_sha256"] != digest((ROOT / ".mithril-runtime-provenance.json").read_bytes())):
        raise Held("release_source_evidence_mismatch")
    for key, name in (("installer", "install-mithril.sh"), ("source_archive", "mithril-cli-runtime.tar.gz")):
        if {k: payload[key][k] for k in ("size", "sha256")} != inventory[name]:
            raise Held("signed_release_asset_mismatch")
    return payload, inventory, data
def next_release(payload, prior):
    if (payload["sequence"] <= prior["sequence"]
        or updates.release_minute(payload["version"]) <= updates.release_minute(prior["version"])
        or prior["runtime_revision"] not in payload["previous_revisions"]):
        raise Held("monotonic_compatible_release_required")
class GitHub:
    def __init__(self, token):
        if not token:
            raise Held("scoped_release_credential_missing")
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(self, method, path, body=None, raw=False, maximum=4*1024*1024):
        if not (path == "/installation/repositories?per_page=100" or path == PREFIX
            or re.fullmatch(re.escape(PREFIX) + r"/(?:git/ref/(?:heads/main|tags/v[0-9A-Za-z.+_-]+)|releases(?:/[1-9][0-9]*|/assets/[1-9][0-9]*|/tags/v[0-9A-Za-z.+_-]+)?)(?:\?per_page=100)?", path)):
            raise Held("provider_path_rejected")
        if method not in ("GET", "POST", "PATCH") or method != "GET" and not (path == PREFIX + "/releases" or re.fullmatch(re.escape(PREFIX)+r"/releases/[1-9][0-9]*", path)):
            raise Held("provider_mutation_rejected")
        req = urllib.request.Request("https://api.github.com"+path, method=method,
            data=canonical(body) if body is not None else None,
            headers={"Authorization":"Bearer "+self.token,"Accept":"application/octet-stream" if raw else "application/vnd.github+json",
                     "X-GitHub-Api-Version":"2026-03-10","User-Agent":"Mithril-CLI-Publisher","Content-Type":"application/json"})
        try:
            response = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as error:
            if raw and error.code == 302:
                url = urllib.parse.urlsplit(error.headers.get("Location", ""))
                if (url.scheme != "https" or url.hostname not in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                    or url.username or url.password or url.port not in (None,443) or url.fragment):
                    raise Held("artifact_redirect_rejected") from None
                # Never forward the GitHub token to artifact storage.
                response = self.opener.open(urllib.request.Request(url.geturl(), headers={"User-Agent":"Mithril-CLI-Publisher"}), timeout=30)
            elif error.code == 404:
                raise Held("provider_not_found") from None
            else:
                raise Held("provider_request_failed") from None
        with response:
            data = response.read(maximum+1)
            if len(data) > maximum:
                raise Held("provider_response_budget")
            if not raw and response.headers.get("Link", "").find('rel="next"') >= 0:
                raise Held("provider_inventory_incomplete")
        return data if raw else json.loads(data)
    def upload(self, release_id, name, data):
        if type(release_id) is not int or release_id < 1 or name not in NAMES:
            raise Held("release_upload_rejected")
        url = "https://uploads.github.com"+PREFIX+"/releases/"+str(release_id)+"/assets?name="+name
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization":"Bearer "+self.token,"Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2026-03-10","User-Agent":"Mithril-CLI-Publisher","Content-Type":"application/octet-stream"})
        with self.opener.open(req, timeout=120) as response:
            raw = response.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise Held("provider_response_budget")
            return json.loads(raw)
def observe(api, sha, payload):
    scoped_authority(api.request("GET", PREFIX), api.request("GET", "/installation/repositories?per_page=100"))
    if api.request("GET", PREFIX+"/git/ref/heads/main").get("object", {}).get("sha") != sha:
        raise Held("remote_main_changed")
    releases = api.request("GET", PREFIX+"/releases?per_page=100")
    if not isinstance(releases, list) or any(r.get("tag_name") == "v"+payload["version"] for r in releases):
        raise Held("release_already_exists")
    # A tag alone is also an unresolved prior attempt; never replace it.
    try:
        tag = api.request("GET", PREFIX+"/git/ref/tags/v"+payload["version"])
    except Held as error:
        # Distinguish a genuine 404 below with the transport; no generic failure-as-absence.
        if str(error) != "provider_not_found":
            raise
    else:
        raise Held("release_tag_already_exists")
    versions = [r for r in releases if not r.get("draft") and r.get("prerelease") and isinstance(r.get("tag_name"), str)
                and updates.VERSION.fullmatch(r["tag_name"][1:])]
    if not versions:
        raise Held("prior_public_preview_required")
    prior_release = max(versions, key=lambda r: updates.release_minute(r["tag_name"][1:]))
    assets = [a for a in prior_release.get("assets", []) if a.get("name") == "mithril-update.json"]
    if len(assets) != 1 or type(assets[0].get("id")) is not int:
        raise Held("prior_signed_metadata_missing")
    prior_bytes = api.request("GET", PREFIX+"/releases/assets/"+str(assets[0]["id"]), raw=True, maximum=1024*1024)
    prior = updates.verify(json.loads(prior_bytes), updates.load_trust())
    if prior_release["tag_name"] != "v"+prior["version"]:
        raise Held("prior_release_identity_mismatch")
    next_release(payload, prior)
    return {"priorReleaseId":prior_release["id"], "priorManifestSha256":digest(prior_bytes)}
def exact_release(api, release, sha, payload, inventory, draft):
    if (type(release.get("id")) is not int or release.get("draft") is not draft or release.get("prerelease") is not True
        or release.get("tag_name") != "v"+payload["version"] or release.get("target_commitish") != sha):
        raise Held("release_readback_mismatch")
    rows = release.get("assets", [])
    if len(rows) != len(inventory) or {a.get("name") for a in rows} != set(inventory):
        raise Held("release_assets_mismatch")
    for asset in rows:
        wanted = inventory[asset["name"]]
        if asset.get("state") != "uploaded" or asset.get("size") != wanted["size"] or type(asset.get("id")) is not int:
            raise Held("release_assets_mismatch")
        raw = api.request("GET", PREFIX+"/releases/assets/"+str(asset["id"]), raw=True, maximum=wanted["size"])
        if digest(raw) != wanted["sha256"]:
            raise Held("release_public_bytes_mismatch")
def write_state(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(canonical(value)+b"\n")
    path.chmod(0o600)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight","publish"))
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    args=parser.parse_args()
    journal={"schema":1,"status":"held","publicationCompleted":False}
    journal_path=OUTPUT / "journal.json"
    if journal_path.exists() or journal_path.is_symlink():
        print(json.dumps({"status":"held","reason":"prior_publication_requires_reconciliation","publicationCompleted":False}))
        return 1
    try:
        sha=identity()
        payload,inventory,data=verified_assets(args.assets,sha,os.environ["CIRCLE_WORKFLOW_ID"],args.version,args.sequence)
        api=GitHub(release_token())
        context={"sourceSha":sha,"pipelineId":os.environ["CIRCLE_PIPELINE_ID"],"workflowId":os.environ["CIRCLE_WORKFLOW_ID"],
                 "version":payload["version"],"sequence":payload["sequence"],"assets":inventory,**observe(api,sha,payload)}
        if args.operation=="preflight":
            if args.state.exists():
                raise Held("preflight_state_already_exists")
            write_state(args.state,context)
            print(json.dumps({"status":"ready-for-approval","sourceSha":sha,"contextSha256":digest(canonical(context))}))
            return 0
        if args.state.is_symlink() or json.loads(args.state.read_bytes()) != context or journal_path.exists():
            raise Held("publication_intent_changed_or_unresolved")
        journal.update(sourceSha=sha,version=payload["version"],workflowId=os.environ["CIRCLE_WORKFLOW_ID"],status="draft-create-attempted")
        write_state(journal_path,journal)
        identity()
        release=api.request("POST",PREFIX+"/releases",{"tag_name":"v"+payload["version"],"target_commitish":sha,"draft":True,
            "prerelease":True,"name":"Mithril CLI "+payload["version"],"body":"Signed Mithril CLI preview from exact source "+sha+". Previous releases and compatible state schemas are preserved."})
        release_id=release.get("id")
        if type(release_id) is not int or release_id<1:
            raise Held("draft_create_outcome_uncertain")
        journal.update(releaseId=release_id,status="upload-attempted")
        write_state(journal_path,journal)
        for name, raw in sorted(data.items()):
            identity()
            api.upload(release_id,name,raw)
        exact_release(api,api.request("GET",PREFIX+"/releases/"+str(release_id)),sha,payload,inventory,True)
        identity()
        journal["status"]="publication-attempted"
        write_state(journal_path,journal)
        api.request("PATCH",PREFIX+"/releases/"+str(release_id),{"draft":False,"make_latest":"false"})
        exact_release(api,api.request("GET",PREFIX+"/releases/"+str(release_id)),sha,payload,inventory,False)
        tag=api.request("GET",PREFIX+"/git/ref/tags/v"+payload["version"])
        if tag.get("object",{}).get("type")!="commit" or tag["object"]["sha"]!=sha:
            raise Held("published_tag_source_mismatch")
        # Independent unauthenticated public reads exercise the ordinary updater origin.
        for name,wanted in inventory.items():
            raw=updates.download("https://github.com/"+REPO+"/releases/download/v"+payload["version"]+"/"+name,wanted["size"])
            if len(raw)!=wanted["size"] or digest(raw)!=wanted["sha256"]:
                raise Held("anonymous_public_readback_mismatch")
        identity()
        journal.update(status="published-verified",publicationCompleted=True,assets=inventory)
        write_state(journal_path,journal)
        print(json.dumps({"status":journal["status"],"sourceSha":sha,"version":payload["version"],"releaseId":release_id,"publicationCompleted":True}))
        return 0
    except Exception as error:
        journal["reason"]=str(error) if isinstance(error,Held) else "publication_held"
        write_state(journal_path,journal)
        print(json.dumps(journal))
        return 1
if __name__=="__main__":
    raise SystemExit(main())
