"""Checks that Homebrew and the shell installer publish one pinned runtime."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MithrilDistributionTests(unittest.TestCase):
    def test_distribution_version_is_visible_and_utc_minute_sortable(self):
        formula = (ROOT / "Formula/mithril-agent.rb").read_text(encoding="utf-8")
        installer = (ROOT / "scripts/install-mithril.sh").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts/mithril-brew-launcher").read_text(encoding="utf-8")
        expected = "0.1.0-preview.20261025.t0515"
        self.assertIn(f'version "{expected}"', formula)
        self.assertIn(f"release_version='{expected}'", installer)
        self.assertIn(f"release_version='{expected}'", launcher)
        self.assertIn('-V|--version) update_module version --fallback-version "$release_version"', launcher)
        self.assertIn('-V|--version) update_module -p "${2:-}" version --fallback-version "$release_version"', launcher)

    def test_homebrew_and_shell_installer_pin_the_same_runtime_commit(self):
        formula = (ROOT / "Formula/mithril-agent.rb").read_text(encoding="utf-8")
        installer = (ROOT / "scripts/install-mithril.sh").read_text(encoding="utf-8")
        formula_revision = re.search(r'revision:\s*"([0-9a-f]{40})"', formula)
        installer_revision = re.search(r"^revision='([0-9a-f]{40})'$", installer, re.MULTILINE)
        self.assertIsNotNone(formula_revision)
        self.assertIsNotNone(installer_revision)
        self.assertEqual(formula_revision.group(1), installer_revision.group(1))

    def test_shell_installer_bootstrap_digest_matches_the_pinned_script(self):
        installer = (ROOT / "scripts/install-mithril.sh").read_text(encoding="utf-8")
        digest = re.search(r"^installer_sha256='([0-9a-f]{64})'$", installer, re.MULTILINE)
        self.assertIsNotNone(digest)
        revision = installer_revision_from_shell(ROOT / "scripts/install-mithril.sh")
        # A distribution wrapper can pin an earlier qualified runtime commit.
        # Verify that immutable child's bytes, not the current working tree.
        bootstrap = subprocess.check_output(["git", "show", f"{revision}:scripts/install.sh"], cwd=ROOT)
        actual = hashlib.sha256(bootstrap).hexdigest()
        self.assertEqual(digest.group(1), actual)

    def test_shell_installer_syntax_and_dry_run(self):
        script = ROOT / "scripts/install-mithril.sh"
        syntax = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "profile"
            runtime = Path(temporary) / "runtime"
            bindir = Path(temporary) / "bin"
            result = subprocess.run(
                ["sh", str(script), "--dry-run", "--home", str(home),
                 "--dir", str(runtime), "--bin-dir", str(bindir)],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Revision: " + installer_revision_from_shell(script), result.stdout)


def installer_revision_from_shell(script: Path) -> str:
    match = re.search(r"^revision='([0-9a-f]{40})'$", script.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise AssertionError("shell installer revision pin is missing")
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
