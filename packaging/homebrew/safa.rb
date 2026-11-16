# Homebrew formula — beta macOS (Apple Silicon) tarball install.
# Replace <owner>/<sha256> at release; asset names come from release-info.toml.
class Safa < Formula
  desc "Clean audio and video recordings locally (صفا)"
  homepage "https://github.com/<owner>/safa"
  url "https://github.com/<owner>/safa/releases/download/v0.1.0/Safa-0.1.0-macos-arm64.tar.gz"
  sha256 "<sha256-macos>"
  license "MIT"

  depends_on arch: :arm64
  depends_on :macos

  def install
    # The tarball is a self-contained portable tree (python/, backend/,
    # frontend/dist/, tools/ffmpeg/) — install it wholesale.
    libexec.install Dir["*"]
    (bin/"safa").write <<~EOS
      #!/bin/sh
      cd "#{libexec}"
      exec "#{libexec}/python/bin/python3" -m backend.launcher "$@"
    EOS
    chmod 0755, bin/"safa"
  end

  def post_install
    data = Pathname.new(Dir.home) / "Library/Application Support/Safa"
    data.mkpath
    (libexec/"installed.json").write(
      { data_dir: data.to_s, variant: "cpu" }.to_json + "\n",
    ) unless (libexec/"installed.json").exist?
  end

  def caveats
    <<~EOS
      Start Safa with:  safa
      Data lives in:    ~/Library/Application Support/Safa
      Models (~600 MB) download on first run.
    EOS
  end

  test do
    system "#{bin}/safa", "--check-only"
  end
end
