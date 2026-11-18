# Homebrew formula — beta macOS (Apple Silicon) tarball install.
# url + sha256 are filled by scripts/bump_manifests.py --sums SHA256SUMS.txt
# after a release is published.
class Safa < Formula
  desc "Clean audio and video recordings locally (صفا)"
  homepage "https://github.com/ahmednajibe/safa"
  url "https://github.com/ahmednajibe/safa/releases/download/v0.1.1/Safa-0.1.1-macos-arm64.tar.gz"
  sha256 "2e24b0b0711632e80ba70ae37eb58d347648702b6aa60f9c97e3ee0f6c642eba"
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
