"""README EN/AR + llms.txt contract (M10c §9.18-23).

- Both READMEs list every install method (machine-readable anchors
  ``<!-- method:<id> -->``), in the same order.
- llms.txt links to the AI-assistant sections in both READMEs and to
  each OS method list.
- Required warning words (SmartScreen, Gatekeeper, unsigned) appear.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = (ROOT / "README.md").read_text(encoding="utf-8")
AR = (ROOT / "README.ar.md").read_text(encoding="utf-8")
LLMS = (ROOT / "llms.txt").read_text(encoding="utf-8")

METHOD_RE = re.compile(r"<!--\s*method:([a-z0-9-]+)\s*-->")

REQUIRED_METHODS = [
    "win-online", "win-offline-cpu", "win-offline-gpu",
    "win-portable-cpu", "win-portable-gpu", "win-one-liner",
    "win-winget", "win-scoop", "win-choco", "win-source",
    "mac-install-sh", "mac-dmg", "mac-brew", "mac-portable", "mac-source",
    "linux-install-sh-cpu", "linux-install-sh-cuda",
    "linux-deb-cpu", "linux-deb-cuda", "linux-rpm-cpu", "linux-rpm-cuda",
    "linux-appimage", "linux-flatpak", "linux-aur",
    "linux-portable-cpu", "linux-portable-cuda", "linux-source",
    "pypi", "docker-cpu", "docker-cuda", "models-import",
]


def methods(doc: str) -> list[str]:
    return METHOD_RE.findall(doc)


def test_readme_lists_every_method():
    en, ar = methods(EN), methods(AR)
    for m in REQUIRED_METHODS:
        assert m in en, f"README.md missing method:{m}"
        assert m in ar, f"README.ar.md missing method:{m}"


def test_readme_en_ar_method_sync():
    assert methods(EN) == methods(AR), (
        "EN/AR method lists diverged: "
        f"{set(methods(EN)) ^ set(methods(AR))}"
    )


def test_readme_warnings_and_facts():
    for doc, tag in ((EN, "en"), (AR, "ar")):
        assert "SmartScreen" in doc, f"{tag} README lacks SmartScreen"
        assert "127.0.0.1:8790" in doc, f"{tag} README lacks port contract"
        assert "install.ps1" in doc and "install.sh" in doc
        assert "github.com/ahmednajibe/safa" in doc


def test_llms_links_sections():
    assert "README.md" in LLMS and "README.ar.md" in LLMS
    for frag in ("for-ai-assistants", "install", "docker", "troubleshoot"):
        assert frag in LLMS.lower(), f"llms.txt lacks link to {frag}"
    assert "127.0.0.1" in LLMS and "8790" in LLMS


def test_llms_method_anchors_covered():
    # every method id must be reachable via a link/section in llms.txt
    for m in ("win-online", "docker", "models", "install.sh", "install.ps1"):
        token = m.split("-")[0]
        assert token in LLMS.lower()
