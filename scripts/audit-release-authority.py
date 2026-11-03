"""Read the original runtime checkout's GitHub authority without mutating GitHub."""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "mithril-lang/mithril-agent-runtime"
class Refused(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("provider_redirect_rejected")

def scope_summary(repository, installation):
    if repository.get("full_name") != REPOSITORY or not isinstance(repository.get("id"), int):
        raise Refused("repository_identity_mismatch")
    permissions = repository.get("permissions", {})
    repos = installation.get("repositories", [])
    count = installation.get("total_count")
    scoped = count == 1 and len(repos) == 1 and repos[0].get("id") == repository["id"] and repos[0].get("full_name") == REPOSITORY
    push = permissions.get("push") is True
    return {"repository": REPOSITORY, "repositoryId": repository["id"],
            "repositoryPush": push, "installationRepositoryCount": count if isinstance(count, int) else None,
            "singleRepositoryScopeVerified": scoped,
            "publisherAuthorityQualified": scoped and push,
            "readOnly": True, "releasePublished": False}

def main():
    result = {"schema": 1, "readOnly": True, "releasePublished": False, "publisherAuthorityQualified": False}
    try:
        if (os.environ.get("CIRCLECI") != "true" or os.environ.get("CIRCLE_BRANCH") != "main" or
            os.environ.get("CIRCLE_PROJECT_USERNAME") + "/" + os.environ.get("CIRCLE_PROJECT_REPONAME") != REPOSITORY):
            raise Refused("original_main_ci_required")
        sha = os.environ.get("CIRCLE_SHA1", "")
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if not re.fullmatch(r"[a-f0-9]{40}", sha) or actual != sha:
            raise Refused("source_identity_mismatch")
        result["sourceSha"] = sha
        # Ask only the original Git credential helper for this exact GitHub checkout.
        child = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\npath=" + REPOSITORY + ".git\n\n",
            text=True, capture_output=True, timeout=15, env={**os.environ, "GIT_TERMINAL_PROMPT":"0"})
        if child.returncode:
            raise Refused("checkout_credential_unavailable")
        fields = dict(line.split("=",1) for line in child.stdout.splitlines() if "=" in line)
        token = fields.get("password", "")
        if not token or fields.get("host") != "github.com" or fields.get("protocol") != "https":
            raise Refused("checkout_credential_unavailable")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        def read(path):
            req = urllib.request.Request("https://api.github.com" + path, headers={
                "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Mithril-runtime-authority-audit"})
            with opener.open(req, timeout=15) as response:
                raw = response.read(1024*1024+1)
                if len(raw) > 1024*1024:
                    raise Refused("provider_response_budget")
                return json.loads(raw)
        repository = read("/repos/" + REPOSITORY)
        try:
            installation = read("/installation/repositories?per_page=100")
        except urllib.error.HTTPError as error:
            if error.code in (401,403,404):
                result.update(scope_summary(repository, {}))
                result["status"] = "scope_unverifiable"
            else:
                raise Refused("provider_read_failed")
        else:
            result.update(scope_summary(repository, installation))
            result["status"] = "observed"
    except Refused as error:
        result["status"] = "held"
        result["reason"] = str(error)
    except Exception:
        # Never emit a credential-helper error, URL, header or provider body.
        result["status"] = "held"
        result["reason"] = "authority_audit_failed"
    destination = Path("/tmp/mithril-release-authority")
    destination.mkdir(mode=0o700, exist_ok=True)
    (destination / "authority.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("observed", "scope_unverifiable") else 1

if __name__ == "__main__":
    raise SystemExit(main())
