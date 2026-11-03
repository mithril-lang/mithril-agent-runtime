import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location("release_authority", Path(__file__).resolve().parents[1] / "scripts/audit-release-authority.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class AuthorityScopeTests(unittest.TestCase):
    def test_only_single_original_repository_with_push_can_qualify(self):
        repo={"id":42,"full_name":module.REPOSITORY,"permissions":{"push":True},"private":"NEVER_EMIT"}
        exact={"total_count":1,"repositories":[{"id":42,"full_name":module.REPOSITORY}]}
        self.assertTrue(module.scope_summary(repo,exact)["publisherAuthorityQualified"])
        for installation in ({},{"total_count":2,"repositories":exact["repositories"]},
            {"total_count":1,"repositories":[{"id":43,"full_name":"other/repo"}]}):
            self.assertFalse(module.scope_summary(repo,installation)["publisherAuthorityQualified"])
        self.assertFalse(module.scope_summary({**repo,"permissions":{"push":False}},exact)["publisherAuthorityQualified"])
        self.assertNotIn("NEVER_EMIT", str(module.scope_summary(repo,exact)))
    def test_foreign_repository_and_redirect_are_refused(self):
        with self.assertRaises(module.Refused):
            module.scope_summary({"id":42,"full_name":"other/repo"},{})
        with self.assertRaises(module.Refused):
            module.NoRedirect().redirect_request(None,None,302,"",{},"https://other.example")
if __name__ == "__main__":
    unittest.main()
