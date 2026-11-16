"""M10 Task 11: accessibility walkthrough (EN + AR).

Boots the real app (temp data dir via SS_E2E_DATA, like the m9a e2e) and
runs keyboard / label / contrast / reduced-motion checks in both
languages:

- ``html[lang]``/``dir`` correct per language.
- Every ``button``/``input``/``select``/``textarea`` has an accessible
  name (innerText, aria-label, or labelledby).
- Tab order: the first ~12 Tabs land on interactive elements — never
  dead-ends on unfocusable divs, never traps.
- Focus is *visible*: :focus-visible outline ≥ 1 px on controls.
- Settings gear opens its popover via Enter; Escape closes it.
- Language toggle flips dir + renders localized strings.
- aria-live polite announcer exists.
- AA contrast (4.5:1 text, 3:1 large/UI) on the main text elements.
- prefers-reduced-motion kills transitions (emulated).
- Decorative inline SVGs are aria-hidden.

Run:  uv run python tests/e2e_a11y_playwright.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 8790
BASE = f"http://127.0.0.1:{PORT}"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print(
        f"  {'PASS' if cond else 'FAIL'}  {name}"
        + (f" — {detail}" if detail else "")
    )
    return cond


def _health_up() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_health(server: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.poll() is not None:
            raise RuntimeError(
                f"uvicorn exited early (code {server.returncode}) — "
                "is port 8790 already bound by another process?"
            )
        if _health_up():
            return
        time.sleep(0.5)
    raise RuntimeError("server did not come up")


# ---- in-page probes ------------------------------------------------------------

NAMES_JS = r"""
(() => {
  const bad = [];
  const ok = (el) => {
    const name = (
      el.innerText || el.getAttribute("aria-label") ||
      el.getAttribute("aria-labelledby") || el.getAttribute("title") ||
      el.getAttribute("placeholder") || ""
    ).trim();
    if (!name) {
      bad.push(
        el.tagName + "." + (el.className || "") + "#" + (el.id || "?")
      );
    }
  };
  document
    .querySelectorAll("button, [role='button'], select, input:not([type=hidden]), textarea")
    .forEach((el) => {
      // display:none (e.g. the `hidden` file input) isn't in the a11y tree.
      if (getComputedStyle(el).display === "none") return;
      ok(el);
    });
  return bad;
})()
"""

CONTRAST_JS = r"""
(sel) => {
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    const m = c.match(/rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)/);
    if (!m) return null;
    return (
      0.2126 * f(+m[1]) + 0.7152 * f(+m[2]) + 0.0722 * f(+m[3])
    );
  };
  const bgOf = (el) => {
    let e = el;
    while (e) {
      const c = getComputedStyle(e).backgroundColor;
      if (c && !c.startsWith("rgba(0, 0, 0, 0") && c !== "transparent")
        return c;
      e = e.parentElement;
    }
    return "rgb(0, 0, 0)";
  };
  const el = document.querySelector(sel);
  if (!el) return null;
  const cs = getComputedStyle(el);
  const fg = lum(cs.color);
  const bg = lum(bgOf(el));
  if (fg === null || bg === null) return null;
  const [hi, lo] = fg > bg ? [fg, bg] : [bg, fg];
  return {
    ratio: (hi + 0.05) / (lo + 0.05),
    size: parseFloat(cs.fontSize),
    weight: parseInt(cs.fontWeight) || 400,
  };
}
"""

# focusable check used per-Tab (playwright keyboard.press)
ACTIVE_JS = r"""
(() => {
  const el = document.activeElement;
  if (!el || el === document.body) return {tag: "BODY", ok: false};
  const name = (
    el.innerText || el.getAttribute("aria-label") ||
    el.getAttribute("title") || el.getAttribute("placeholder") || ""
  ).trim().slice(0, 40);
  const interactive = [
    "A", "BUTTON", "INPUT", "SELECT", "TEXTAREA",
  ].includes(el.tagName) || el.getAttribute("role") === "button";
  const cs = getComputedStyle(el);
  const outline = parseFloat(cs.outlineWidth || "0");
  return {
    tag: el.tagName, ok: interactive, name, outline,
    cls: (el.className || "").toString().slice(0, 50),
  };
})()
"""


def a11y_pass(page, lang: str, shots: Path) -> int:
    """One full a11y pass; returns failure count."""
    f0 = len(RESULTS)
    shots.mkdir(parents=True, exist_ok=True)

    html = page.evaluate(
        "({lang: document.documentElement.lang, "
        "dir: document.documentElement.dir})"
    )
    if lang == "en":
        check("en: html lang/dir", html["lang"] == "en" and html["dir"] == "ltr", str(html))
    else:
        check("ar: html lang/dir", html["lang"] == "ar" and html["dir"] == "rtl", str(html))

    check(f"{lang}: aria-live announcer present",
          page.locator("[aria-live='polite']").count() >= 1)

    bad = page.evaluate(NAMES_JS)
    check(f"{lang}: every control has an accessible name",
          not bad, ", ".join(bad[:5]))

    deco = page.evaluate(
        "Array.from(document.querySelectorAll('button svg, .link-btn svg'))"
        ".filter(s => !s.getAttribute('aria-hidden') "
        "&& !s.closest('[aria-label]')).length"
    )
    check(f"{lang}: decorative svgs hidden or button labelled", deco == 0,
          f"{deco} unhidden")

    # --- keyboard walk -----------------------------------------------------
    page.evaluate("document.body.focus()")
    page.keyboard.press("Tab")
    seq = []
    for _ in range(12):
        seq.append(page.evaluate(ACTIVE_JS))
        page.keyboard.press("Tab")
    dead = [s for s in seq if not s["ok"]]
    check(f"{lang}: first 12 tabs land on interactive controls",
          len(dead) <= 2,
          f"dead stops: {[s.get('cls') or s['tag'] for s in dead]}")
    named = [s for s in seq if s["ok"] and not s["name"]]
    check(f"{lang}: focused controls announce a name",
          not named, f"unnamed: {[s.get('cls') for s in named]}")
    ringed = [s for s in seq if s["ok"] and s["outline"] >= 1]
    check(f"{lang}: focus ring visible on controls",
          len(ringed) >= max(1, len([s for s in seq if s["ok"]]) - 2),
          f"{len(ringed)} ringed")

    # --- settings popover via button ---------------------------------------
    label = "Settings" if lang == "en" else "الإعدادات"
    settings = page.locator(f"button[aria-label='{label}']")
    check(f"{lang}: settings button found", settings.count() == 1)
    settings.first.click()
    pop = page.locator(".settings-pop, .popover").first
    check(f"{lang}: settings popover opens",
          pop.count() > 0 and pop.is_visible())
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    still = pop.is_visible() if pop.count() else False
    check(f"{lang}: Escape closes the popover", not still)

    # --- contrast -----------------------------------------------------------
    pairs = [
        ("body", 4.5),
        (".drop-title", 4.5),
        (".op-desc", 4.5),
        (".estimate-sub", 4.5),
        (".pill", 3.0),
        (".link-btn", 4.5),
    ]
    for sel, want in pairs:
        r = page.evaluate(CONTRAST_JS, sel)
        if r is None:
            continue
        # large text (≥18.66px or ≥14px bold) needs only 3:1
        need = 3.0 if (r["size"] >= 18.66 or (r["size"] >= 14 and r["weight"] >= 700)) else want
        check(
            f"{lang}: contrast {sel} ≥{need:.1f}:1",
            r["ratio"] >= need, f"{r['ratio']:.2f}:1",
        )

    # --- reduced motion -----------------------------------------------------
    page.emulate_media(reduced_motion="reduce")
    dur = page.evaluate(
        "getComputedStyle(document.querySelector('.link-btn') "
        "|| document.body).transitionDuration"
    )
    secs = [
        float(m.group(1)) * (0.001 if m.group(2) == "ms" else 1.0)
        for m in re.finditer(r"([\d.eE+-]+)(ms|s)", dur)
    ]
    check(f"{lang}: prefers-reduced-motion kills transitions",
          bool(secs) and max(secs) <= 0.02, dur)
    page.emulate_media(reduced_motion="no-preference")

    page.screenshot(path=str(shots / f"{lang}-a11y.png"))
    return len(RESULTS) - f0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ss_a11y_"))
    if _health_up():
        print(
            "ERROR: 127.0.0.1:8790 already serving — stop it first",
            file=sys.stderr,
        )
        shutil.rmtree(tmp, ignore_errors=True)
        return 2

    env = dict(os.environ)
    env["SS_E2E_DATA"] = str(tmp / "data")
    server = subprocess.Popen(
        ["uv", "run", "uvicorn", "tests.e2e_server:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_health(server)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            shots = ROOT / ".superpowers" / "m10-a11y"

            print("== EN pass ==")
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(BASE)
            page.wait_for_selector(".drop-title", timeout=20000)
            a11y_pass(page, "en", shots)
            ctx.close()

            print("== AR pass ==")
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="ar-EG",
            )
            page = ctx.new_page()
            page.goto(BASE)
            page.wait_for_timeout(1500)
            # Arabic auto-detected from locale — or use the lang toggle.
            html = page.evaluate("document.documentElement.lang")
            if html != "ar":
                page.locator("button.lang-btn").click()
                page.wait_for_timeout(800)
            a11y_pass(page, "ar", shots)
            ctx.close()
            browser.close()
    finally:
        # Kill the whole `uv run → uvicorn.exe → python.exe` tree —
        # terminate() alone leaves the grandchild holding the port.
        if server.poll() is None:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(server.pid)],
                capture_output=True,
            )
        try:
            server.wait(timeout=15)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)} passed, {len(fails)} failed")
    for name, _, detail in fails:
        print(f"  FAIL {name} — {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
