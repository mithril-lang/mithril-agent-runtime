require "digest"

class MithrilAgent < Formula
  desc "Mithril terminal agent installer based on Hermes"
  homepage "https://console.mithril.fund/agent"
  url "https://github.com/mithril-lang/mithril-agent-runtime.git",
      using: :git,
      revision: "877871662866fec71a28867299825cf051f2a1a0"
  version "0.1.0-preview.20261025.t0515"
  license "MIT"

  def install
    # The source archive predates this tap update; bind its bootstrap to this formula.
    runtime_revision = stable.specs.fetch(:revision)
    inreplace "scripts/install-mithril.sh", /^revision='[0-9a-f]{40}'$/, "revision='#{runtime_revision}'"
    inreplace "scripts/install-mithril.sh", /^release_version='[^']+'$/, "release_version='#{version}'"
    inreplace "scripts/mithril-brew-launcher", /^release_version='[^']+'$/, "release_version='#{version}'"
    bootstrap_sha256 = Digest::SHA256.file("scripts/install.sh").hexdigest
    inreplace "scripts/install-mithril.sh", /^installer_sha256='[0-9a-f]{64}'$/, "installer_sha256='#{bootstrap_sha256}'"
    (libexec/"hermes_cli").install "hermes_cli/mithril_updates.py"
    (libexec/"distributions/mithril").install "distributions/mithril/update-trust.json"
    libexec.install "scripts/mithril-update-bootstrap.py"
    libexec.install "scripts/install-mithril.sh"
    libexec.install "scripts/mithril-brew-launcher" => "mithril"
    bin.write_exec_script libexec/"mithril"
  end

  def caveats
    <<~EOS
      Run mithril to prepare the profile-local runtime on first use.
      The profile defaults to ~/.mithril; existing Hermes state is shared by an alias.
      Provider setup, API keys, and messaging services are not created by installation.
      Use mithril update for the runtime and brew upgrade mithril-agent for this wrapper.
      Sign in to Mithril Fund in your browser with mithril login. The first
      interactive launch also starts this device approval flow when needed.
      Clean runtime updates are applied before chat by default; set
      updates.auto_apply: false in the Profile to disable automatic updates.
      Check the account with mithril account status; use mithril logout to sign out locally.
      Container sessions use API-managed Profiles through mithril session.
    EOS
  end

  test do
    assert_match "Mithril Agent installer", shell_output("#{bin}/mithril --installer-help")
    assert_match "Revision: #{stable.specs.fetch(:revision)}",
                 shell_output("sh #{libexec}/install-mithril.sh --dry-run")
    refute_path_exists testpath/".mithril"
  end
end
