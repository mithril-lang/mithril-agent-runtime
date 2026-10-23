class MithrilAgent < Formula
  desc "Mithril terminal agent installer based on Hermes"
  homepage "https://console.mithril.fund/agent"
  url "https://github.com/mithril-lang/mithril-agent-runtime.git",
      using: :git,
      revision: "283efea522e4b9769fa76d8f6236db92310e6642"
  version "0.1.0-preview.6"
  license "MIT"

  def install
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
    refute_path_exists testpath/".mithril"
  end
end
