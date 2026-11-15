"""M9a frontend E2E — Python Playwright on the BUILT app.

Serves ``frontend/dist`` + the real API through ``uvicorn`` on 127.0.0.1:8790
with a throwaway ``SS_E2E_DATA`` dir (never touches ``data/``). Covers:
Arabic-named MP4 upload → Preparing → inspector; podcast preset; karaoke
conflict locks; preview submit; full job; audio A/B with cut-jump;
stubbed Save-as/Save-all pickers; keyboard-only flow; settings auto-save
via PUT path; RTL geometry + no horizontal overflow at 1440/800/390 in
EN and AR; rack containment bounds; AR transport x-order; no-overlap in
the compact dock at 390; fresh-tab session isolation; the results panel
and the grouped save cluster. Screenshots land in ``.superpowers/m9a/``.

Run:  uv run python tests/e2e_m9a_playwright.py
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
SHOTS = ROOT / ".superpowers" / "m9a"
FIX = ROOT / "tests" / "fixtures" / "generated"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    return cond


def _health_up() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_health(server: subprocess.Popen, timeout: float = 60.0) -> None:
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


def wait_job(page, want_kind: str | None = None, timeout: float = 300.0):
    """Poll newest job via REST until terminal; returns the job dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = page.request.get(f"{BASE}/api/jobs")
        jobs = resp.json()["jobs"]
        if jobs:
            j = jobs[0]
            if (want_kind is None or j["kind"] == want_kind) and j["state"] in (
                "completed", "failed", "cancelled", "interrupted",
            ):
                return j
        time.sleep(1.0)
    raise TimeoutError(f"job never settled (kind={want_kind})")


PICKER_STUB = r"""
window.__savedFiles = {};
window.__savedOrder = [];
window.showSaveFilePicker = async (opts) => {
  const name = (opts && opts.suggestedName) || "out.bin";
  const chunks = [];
  return {
    name,
    createWritable: async () => new WritableStream({
      write(chunk) { chunks.push(chunk); },
      close() {
        window.__savedFiles[name] = chunks.reduce((a, c) => a + c.byteLength, 0);
        window.__savedOrder.push(name);
      },
      abort() {},
    }),
  };
};
window.showDirectoryPicker = async () => ({
  getFileHandle: async (name, opts2) => {
    if (!opts2 || !opts2.create) {
      if (!(name in window.__savedFiles)) {
        throw new DOMException("not found", "NotFoundError");
      }
    }
    const chunks = [];
    return {
      name,
      createWritable: async () => new WritableStream({
        write(chunk) { chunks.push(chunk); },
        close() {
          window.__savedFiles[name] = chunks.reduce((a, c) => a + c.byteLength, 0);
          window.__savedOrder.push(name);
        },
        abort() {},
      }),
    };
  },
});
"""


def src_to_out(tl: dict, t: float):
    for seg in tl["segments"]:
        if t < seg["src_start"]:
            return None
        if t <= seg["src_end"]:
            src = min(max(t, seg["src_start"]), seg["src_end"])
            return seg["out_start"] + (src - seg["src_start"]) / seg["speed"]
    return None


def next_kept_src(tl: dict, t: float):
    for seg in tl["segments"]:
        if t <= seg["src_end"]:
            return max(t, seg["src_start"])
    return None


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), full_page=False)
    print(f"  shot  {name}")


def no_overflow(page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )


def rack_bounds(page):
    """Every element inside .rack stays inside the rack horizontally:
    right ≤ rack right, left ≥ rack left — in both directions."""
    return page.evaluate(
        """() => {
          const rack = document.querySelector('.rack');
          if (!rack) return {ok:false, bad:['no .rack element']};
          const rr = rack.getBoundingClientRect();
          const bad = [];
          rack.querySelectorAll('*').forEach(el => {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) return;
            if (r.right > rr.right + 1 || r.left < rr.left - 1) {
              bad.push(el.tagName + '.' + String(el.className).split(' ')[0]
                + ' L' + Math.round(r.left) + ' R' + Math.round(r.right)
                + ' rack ' + Math.round(rr.left) + '..' + Math.round(rr.right));
            }
          });
          return {ok: !bad.length, bad: bad.slice(0, 8)};
        }"""
    )


def no_overlaps_in(page, sel: str):
    """No two non-nested visible elements inside `sel` overlap (bbox)."""
    return page.evaluate(
        """(sel) => {
          const root = document.querySelector(sel);
          if (!root) return {ok:false, bad:['no ' + sel]};
          const els = [...root.querySelectorAll('*')].filter(el => {
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            return r.width > 1 && r.height > 1
              && cs.visibility !== 'hidden' && cs.display !== 'none';
          });
          const bad = [];
          for (let i = 0; i < els.length; i++) {
            const a = els[i].getBoundingClientRect();
            for (let j = i + 1; j < els.length; j++) {
              if (els[i].contains(els[j]) || els[j].contains(els[i])) continue;
              const b = els[j].getBoundingClientRect();
              const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
              if (ox > 1 && oy > 1) {
                bad.push(els[i].tagName + '.' + String(els[i].className)
                  + ' × ' + els[j].tagName + '.' + String(els[j].className));
              }
            }
          }
          return {ok: !bad.length, bad: bad.slice(0, 8)};
        }""",
        sel,
    )


def expand_ops(page, ids) -> None:
    """Turn on each op card whose switch is enabled (skips unavailable)."""
    for oid in ids:
        card = page.locator(f'[data-op-id="{oid}"]')
        sw = card.locator(".switch")
        try:
            on = "on" in (card.get_attribute("class") or "").split()
            if sw.is_enabled() and not on:
                sw.click()
                page.wait_for_timeout(200)
        except Exception:
            pass
    page.wait_for_timeout(700)


def wait_count(page, selector: str, minimum: int, timeout_ms: int = 6000) -> int:
    """Poll a locator count until it reaches `minimum` (resolve round-trips
    can exceed a fixed wait when the worker is busy)."""
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    n = 0
    while time.monotonic() < deadline:
        n = page.locator(selector).count()
        if n >= minimum:
            break
        page.wait_for_timeout(200)
    return n


def upload_file(page, path: Path) -> None:
    page.set_input_files("input[type=file]", str(path))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ss-e2e-"))
    out_dir = tmp / "autosave"
    out_dir.mkdir()
    ar_name = tmp / "تسجيل عربي.mp4"
    shutil.copyfile(FIX / "clip.mp4", ar_name)

    # Regenerate every screenshot from scratch.
    if SHOTS.exists():
        for f in SHOTS.glob("*.png"):
            f.unlink()

    # Refuse to run against a stale/foreign server: 8790 is the production
    # port — if anything already answers, stop and report rather than
    # silently testing the wrong data dir.
    if _health_up():
        print("ERROR: 127.0.0.1:8790 already serving — stop it first", file=sys.stderr)
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
    failures = 0
    try:
        wait_health(server)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ---------- pass 1: EN @1440 full flow ----------
            print("== EN 1440: upload → preset → conflicts → preview → job → save ==")
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.add_init_script(PICKER_STUB)
            page.goto(BASE)
            page.wait_for_selector(".drop-title", timeout=20000)
            check("en: empty-state explains first action",
                  "Drop audio or video" in page.inner_text("body"))
            check("en: no h-overflow", no_overflow(page))
            sub = page.locator(".estimate-sub").inner_text()
            check("en: dock subline invites adding a file",
                  sub.strip() == "Add a file to start", sub.strip())
            check("en: 0 ops → no preset chip selected",
                  page.locator(".preset-bar .chip-btn.on").count() == 0)
            gear = page.evaluate(
                "(() => { const r = document.querySelector("
                "'button[aria-label=\"Settings\"] svg')?.getBBox();"
                " return r ? {width: r.width, height: r.height} : null; })()")
            check("en: settings icon is a real gear (not a dot)",
                  bool(gear) and gear["width"] >= 10 and gear["height"] >= 10,
                  str(gear))
            pill = page.locator(".hardware-pill").inner_text().strip()
            check("en: hardware pill readable",
                  bool(re.search(r"GPU ready|CPU only", pill))
                  and "NVIDIA" not in pill,
                  pill)
            shot(page, "en-1440-empty.png")

            upload_file(page, ar_name)
            try:
                page.wait_for_selector("text=Preparing file", timeout=8000)
                check("en: preparing state shown", True)
            except Exception:
                check("en: preparing state shown",
                      page.wait_for_selector(".inspector", timeout=30000) is not None,
                      "preparing too fast to observe; inspector reached")
            page.wait_for_selector(".inspector", timeout=60000)
            check("en: inspector chips after ready",
                  page.locator(".inspector .chip").count() >= 3)
            check("en: keep-video label is sentence case",
                  page.locator(".kv-row", has_text="Keep video").count() == 1)

            # podcast preset → at least one enabled card
            page.get_by_role("button", name="Podcast cleanup").click()
            page.wait_for_timeout(700)
            n_on = page.locator(".op-card.on").count()
            check("en: podcast preset enables cards", n_on >= 1, f"{n_on} on")

            # karaoke on top of the podcast speech ops → conflict pills lock
            # the speech cards (Karaoke preset alone only enables `separate`).
            page.locator('[data-op-id="separate"] .switch').click()
            page.wait_for_selector('[data-op-id="separate"].op-card.on',
                                   timeout=5000)
            seg = page.locator('[data-op-id="separate"] .seg-opt')
            seg.filter(has_text="Keep music").click()
            n_conf = wait_count(page, ".conflict-pill", 1)
            check("en: karaoke locks speech ops with reason pills", n_conf >= 1,
                  f"{n_conf} conflict pills")
            page.locator(".conflict-pill").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "en-1440-karaoke-conflicts.png")

            # back off → light full job for save/compare path
            page.get_by_role("button", name="Turn all off").click()
            page.wait_for_timeout(400)
            page.locator('[data-op-id="loudness"] .switch').click()
            page.wait_for_timeout(600)
            check("en: loudness card on", page.locator(
                '[data-op-id="loudness"].op-card.on').count() == 1)

            # settings: auto-save folder via PUT (no native dialog)
            r = page.request.put(
                f"{BASE}/api/settings/output-dir",
                data=json.dumps({"path": str(out_dir)}),
                headers={"Content-Type": "application/json"},
            )
            check("settings: PUT output-dir accepted", r.ok, r.text()[:120])

            page.get_by_role("button", name="Process", exact=True).click()
            job = wait_job(page, "full")
            check("en: full job completed", job["state"] == "completed",
                  f"state={job['state']}")
            page.wait_for_selector(".save-cluster", timeout=30000)

            files = job.get("files") or []
            check("save: outputs listed", len(files) >= 1, f"{len(files)} files")
            saved_paths = [f.get("saved_path") for f in files if f.get("saved_path")]
            check("settings: auto-save wrote saved_path", bool(saved_paths),
                  str(saved_paths[:1]))
            check("settings: files landed in folder",
                  any((out_dir / Path(p).name).exists() for p in saved_paths))
            check("dock: Show in folder offered",
                  page.locator(".save-cluster .link-btn").count() >= 1)

            # Done line is plural-aware ("Done · N files"), Saved is a
            # neutral pill, the saved path is middle-truncated in a bdi.
            done_txt = page.locator(".job-result .dock-msg").first.inner_text()
            check("done: 'Done · N files' line",
                  bool(re.match(rf"^Done · {len(files)} file", done_txt.strip())),
                  done_txt.strip())
            check("save: Saved shown as neutral pill (not a filled button)",
                  page.locator(".save-cluster .pill",
                               has_text="Saved").count() >= 1
                  and page.locator(".save-cluster .btn-primary",
                                   has_text="Saved").count() == 0)
            note = page.locator(".save-note bdi")
            check("save: saved path middle-truncated in <bdi dir=ltr>",
                  note.count() == 1 and "…" in note.inner_text()
                  and note.get_attribute("dir") == "ltr",
                  note.inner_text() if note.count() else "no bdi")

            # Results panel: every stage in order + device pill + seconds.
            n_rows = page.locator(".result-row").count()
            check("results: one row per stage",
                  n_rows == len(job.get("stages") or []),
                  f"{n_rows} rows vs {len(job.get('stages') or [])} stages")
            check("results: device pill + seconds per stage",
                  page.locator(".result-row .pill").count() == n_rows
                  and page.locator(".result-secs").count() == n_rows)

            # Save-as via stubbed picker (no OS dialog)
            page.locator('.save-cluster button:has-text("Save")').first.click()
            page.wait_for_timeout(1500)
            saved = page.evaluate("window.__savedFiles")
            check("save: streamed file via stubbed picker",
                  len(saved) >= 1 and all(v > 0 for v in saved.values()),
                  str(saved))
            page.locator(".results").scroll_into_view_if_needed()
            shot(page, "en-1440-completed.png")

            # A/B exists after a completed job
            check("ab: Original/Processed segmented shown",
                  page.locator(".transport .seg-opt").count() == 2)

            # keyboard-only transport
            page.keyboard.press("Space")
            page.wait_for_timeout(400)
            playing = page.evaluate(
                "![...document.querySelectorAll('audio')].every(a => a.paused)")
            check("kbd: Space plays", playing)
            page.keyboard.press("Space")
            page.wait_for_timeout(200)
            t0 = page.evaluate("document.querySelector('audio').currentTime")
            page.keyboard.press("Alt+BracketRight")
            page.wait_for_timeout(200)
            t1 = page.evaluate("document.querySelector('audio').currentTime")
            check("kbd: Alt+] seeks +5s", t1 > t0, f"{t0:.2f}→{t1:.2f}")
            page.keyboard.press("Alt+BracketLeft")
            page.wait_for_timeout(200)
            t2 = page.evaluate("document.querySelector('audio').currentTime")
            check("kbd: Alt+[ seeks −5s", t2 < t1, f"{t1:.2f}→{t2:.2f}")
            page.keyboard.press("KeyB")
            page.wait_for_timeout(300)
            b_checked = page.evaluate(
                "[...document.querySelectorAll('.transport .seg-opt')]"
                ".find(b => b.getAttribute('aria-checked') === 'true')?.textContent")
            check("kbd: KeyB selects Processed", b_checked == "Processed",
                  str(b_checked))
            page.keyboard.press("KeyA")
            page.wait_for_timeout(200)

            # Rack containment: loudness + separate + silence + subtitles
            # expanded — nothing may stick out of the rack, EN 1440 + 800.
            expand_ops(page, ["separate", "silence", "subtitles", "loudness"])
            rb = rack_bounds(page)
            check("en: rack bounds @1440 (4 cards expanded)", rb["ok"],
                  "; ".join(rb["bad"][:3]))
            page.set_viewport_size({"width": 800, "height": 900})
            page.wait_for_timeout(500)
            rb = rack_bounds(page)
            check("en: rack bounds @800 (4 cards expanded)", rb["ok"],
                  "; ".join(rb["bad"][:3]))
            check("en: no h-overflow @800", no_overflow(page))
            shot(page, "en-800-completed.png")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(600)
            ov = no_overlaps_in(page, ".dock")
            check("en: no overlapping elements inside dock @390", ov["ok"],
                  "; ".join(ov["bad"][:3]))
            check("en: no h-overflow @390", no_overflow(page))
            short_pill = page.locator(".hardware-pill").inner_text().strip()
            check("en: hardware pill @390 is GPU/CPU (never a fragment)",
                  short_pill in ("GPU", "CPU"), short_pill)
            page.locator(".results").scroll_into_view_if_needed()
            shot(page, "en-390-completed.png")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(500)

            # ---------- pass 2: AR @1440 geometry (same loaded page) ----------
            print("== AR 1440: RTL geometry ==")
            page.locator(".lang-btn").click()
            page.wait_for_function("document.documentElement.dir === 'rtl'")
            page.wait_for_timeout(800)
            rack = page.locator(".rack").bounding_box()
            player = page.locator(".player-col").bounding_box()
            check("ar: dir=rtl + rack right of player",
                  rack and player and rack["x"] > player["x"],
                  f"rack.x={rack and rack['x']} player.x={player and player['x']}")
            check("ar: no h-overflow", no_overflow(page))
            check("ar: transport timecode uses western digits",
                  all(ch.isascii() for ch in
                      page.locator(".tc").first.inner_text().strip()))

            # Transport is a dir=ltr island: play < bar < duration even in AR.
            order = page.evaluate(
                """() => {
                  const bx = (el) => el && el.getBoundingClientRect().x;
                  const play = bx(document.querySelector('.play-btn'));
                  const bar = bx(document.querySelector('.playback'));
                  const tcs = [...document
                    .querySelectorAll('.transport-island .tc')]
                    .map(e => e.getBoundingClientRect().x);
                  return {play, cur: tcs[0], bar, dur: tcs[1],
                          islandDir: document.querySelector(
                            '.transport-island')?.dir};
                }""")
            check("ar: transport island is dir=ltr",
                  order.get("islandDir") == "ltr", str(order))
            check("ar: play < bar < duration (transport not mirrored)",
                  order["play"] is not None and order["bar"] is not None
                  and order["dur"] is not None
                  and order["play"] < order["bar"] < order["dur"],
                  str(order))

            rb = rack_bounds(page)
            check("ar: rack bounds @1440 (4 cards expanded)", rb["ok"],
                  "; ".join(rb["bad"][:3]))

            page.locator(".results").scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            check("ar: results panel shows after job",
                  page.locator(".result-row").count() >= 1)
            shot(page, "ar-1440-completed.png")

            # AR bounds at 800 too
            page.set_viewport_size({"width": 800, "height": 900})
            page.wait_for_timeout(500)
            rb = rack_bounds(page)
            check("ar: rack bounds @800 (4 cards expanded)", rb["ok"],
                  "; ".join(rb["bad"][:3]))
            check("ar: no h-overflow @800", no_overflow(page))
            page.locator(".results").scroll_into_view_if_needed()
            shot(page, "ar-800-completed.png")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(500)

            # karaoke conflicts in AR
            page.locator('[data-op-id="separate"] .seg-opt').filter(
                has_text="كاريوكي").click()
            n_conf = wait_count(page, ".conflict-pill", 1)
            check("ar: karaoke locks speech ops with reason pills",
                  n_conf >= 1, f"{n_conf} conflict pills")
            page.locator(".conflict-pill").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "ar-1440-karaoke-conflicts.png")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(600)
            ov = no_overlaps_in(page, ".dock")
            check("ar: no overlapping elements inside dock @390", ov["ok"],
                  "; ".join(ov["bad"][:3]))
            check("ar: no h-overflow @390", no_overflow(page))
            page.locator(".results").scroll_into_view_if_needed()
            shot(page, "ar-390-completed.png")
            ctx.close()

            # ---------- pass 3: fresh tabs (empty state / session isolation) --
            print("== fresh tabs: empty state + session isolation ==")
            for w, h, lang, tag in (
                (1440, 900, "ar", "ar-1440-empty"),
                (800, 900, "en", "en-800-empty"),
                (800, 900, "ar", "ar-800-empty"),
                (390, 844, "en", "en-390-empty"),
                (390, 844, "ar", "ar-390-empty"),
            ):
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                page.add_init_script(PICKER_STUB)
                page.goto(BASE)
                page.wait_for_selector(".drop-title", timeout=20000)
                if lang == "ar":
                    page.locator(".lang-btn").click()
                    page.wait_for_function(
                        "document.documentElement.dir === 'rtl'")
                page.wait_for_timeout(800)
                check(f"{tag}: no h-overflow", no_overflow(page))
                check(f"{tag}: fresh tab shows no stale results",
                      page.locator(".save-cluster").count() == 0
                      and page.locator(".job-result .dock-msg").count() == 0)
                if w <= 390:
                    ov = no_overlaps_in(page, ".dock")
                    check(f"{tag}: no overlapping elements inside dock",
                          ov["ok"], "; ".join(ov["bad"][:3]))
                shot(page, f"{tag}.png")
                ctx.close()

            # ---------- pass 4: preview + cut-jump (EN 1440) ----------
            print("== EN 1440: silence preview → A/B cut jump ==")
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.add_init_script(PICKER_STUB)
            page.goto(BASE)
            page.wait_for_selector(".drop-title, .inspector", timeout=20000)
            upload_file(page, FIX / "speech_silences.wav")
            page.wait_for_selector(".inspector", timeout=60000)

            # prominent preview UI (silence-remove picked → hint shown)
            page.locator('[data-op-id="silence"] .switch').click()
            page.wait_for_timeout(900)
            check("preview: hint promoted for silence(remove)",
                  page.locator(".preview-hint").count() == 1)

            dur = page.evaluate(
                "document.querySelector('audio').duration")
            end = min(20.0, float(dur) if dur == dur else 20.0)
            fields = page.locator(".preview-fields input.num-field")
            fields.nth(0).fill("0")
            fields.nth(1).fill(str(int(end)))
            page.locator(".preview-fields .btn-secondary").click()
            pjob = wait_job(page, "preview")
            check("preview: job completed", pjob["state"] == "completed",
                  f"state={pjob['state']}")
            page.wait_for_selector(".transport .seg", timeout=30000)

            tl = page.request.get(
                f"{BASE}/api/jobs/{pjob['id']}/timeline").json()
            removed = tl.get("removed") or []
            check("timeline: preview has removed spans", len(removed) >= 1,
                  f"{len(removed)} spans")
            if removed:
                a, b = removed[0]
                mid = (a + b) / 2
                nks = next_kept_src(tl, mid)
                expected = src_to_out(tl, nks) if nks is not None else 0.0
                page.evaluate(
                    "document.querySelectorAll('audio')[0].currentTime = "
                    + repr(float(mid)))
                page.locator(".transport .seg-opt").nth(1).click()
                page.wait_for_timeout(500)
                got = page.evaluate(
                    "document.querySelectorAll('audio')[1].currentTime")
                check("ab: switch inside cut jumps to next kept point",
                      abs(got - expected) < 0.35,
                      f"mid={mid:.2f} expected≈{expected:.2f} got={got:.2f}")
                live = page.locator("[aria-live]").last.inner_text()
                check("ab: jump announced",
                      "Jumped to" in live or "removed" in live,
                      live.strip()[:80])
            shot(page, "en-1440-preview-ab.png")
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

    failed = [r for r in RESULTS if not r[1]]
    print("\n==== E2E RESULT ====")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name, _, detail in failed:
        print(f"  FAIL {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
