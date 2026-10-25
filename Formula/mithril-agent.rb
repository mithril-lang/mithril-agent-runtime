require "digest"

class MithrilAgent < Formula
  desc "Mithril terminal agent installer based on Hermes"
  homepage "https://console.mithril.fund/agent"
  url "https://github.com/mithril-lang/mithril-agent-runtime.git",
      using: :git,
      revision: "f8ff45e9852284d9d58e133c7fc73031a6938123"
  version "0.1.0-preview.7"
  license "MIT"

  def install
    # The source archive predates this tap update; bind its bootstrap to this formula.
    runtime_revision = stable.specs.fetch(:revision)
    inreplace "scripts/install-mithril.sh", /^revision='[0-9a-f]{40}'$/, "revision='#{runtime_revision}'"
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
      The first interactive launch asks you to sign in to Mithril Fund in your browser.
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
