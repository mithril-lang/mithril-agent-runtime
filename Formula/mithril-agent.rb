class MithrilAgent < Formula
  desc "Mithril terminal agent installer based on Hermes"
  homepage "https://console.mithril.fund/agent"
  url "https://github.com/mithril-lang/mithril-agent-runtime.git",
      using: :git,
      revision: "35aa26f15a273f7d3cb974262bdc2ba262bb2d0b"
  version "0.1.0-preview.3"
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
      Container sessions use API-managed Profiles through mithril session.
    EOS
  end

  test do
    assert_match "Mithril Agent installer", shell_output("#{bin}/mithril --installer-help")
    refute_path_exists testpath/".mithril"
  end
end
