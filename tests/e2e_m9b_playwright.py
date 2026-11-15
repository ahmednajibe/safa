"""M9b frontend E2E — Python Playwright on the BUILT app.

Serves ``frontend/dist`` + the real API through ``uvicorn`` on
127.0.0.1:8790 with a throwaway ``SS_E2E_DATA`` dir (never touches
``data/``). Covers, in EN and AR at 1440/800/390:

- waveform well: wavesurfer renders backend peaks (grey original),
  ``dir="ltr"`` island, ruler ticks, zoom buttons;
- preview-region handles: drag ↔ numeric field sync, ``[``/``]`` nudge
  0.5 s, ``P`` plays just the region and stops at its end;
- preview job → 45° cut hatches + the textual cut list, A/B cut-jump
  through the preview source offset, processed wave in --data amber;
- progress bar: ``overall_pct`` fill — monotonic and past (n-1)/N;
- video well on clip.mp4: muted video mirrors the transport side +
  playhead (Range-seeked), A/B flips the frame;
- RTL switch parity: ON = white track + dark knob at inline-end, mirrored
  vs EN; the well/ruler/transport stay LTR inside dir=rtl;
- keyboard via event.code: Space, KeyA/KeyB, ArrowLeft/Right, KeyP,
  BracketLeft/Right — including under the Arabic layout.

Screenshots land in ``.superpowers/m9b/``.

Run:  uv run python tests/e2e_m9b_playwright.py
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
SHOTS = ROOT / ".superpowers" / "m9b"
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


def wait_job(page, want_kind: str | None = None, timeout: float = 300.0,
             sample_progress: list | None = None):
    """Poll newest job via REST until terminal; returns the job dict.
    While waiting, samples the dock's running progress bar (fill width +
    step text) into ``sample_progress`` when a list is passed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sample_progress is not None:
            sample = page.evaluate(
                """() => {
                  const fill = document.querySelector(
                    '.dock-running .progress-fill');
                  const meta = document.querySelector('.dock-running .run-meta');
                  if (!fill) return null;
                  return {w: parseFloat(fill.style.inlineSize || '0'),
                          meta: meta ? meta.innerText : ''};
                }""")
            if sample:
                sample_progress.append(sample)
        resp = page.request.get(f"{BASE}/api/jobs")
        jobs = resp.json()["jobs"]
        if jobs:
            j = jobs[0]
            if (want_kind is None or j["kind"] == want_kind) and j["state"] in (
                "completed", "failed", "cancelled", "interrupted",
            ):
                return j
        time.sleep(0.15)
    raise TimeoutError(f"job never settled (kind={want_kind})")


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


def upload_file(page, path: Path) -> None:
    page.set_input_files("input[type=file]", str(path))


def switch_probe(page):
    """On-switch geometry: track bg, knob colour, knob centre-x as a
    fraction of track width (inline-end ≈1 in LTR, ≈0 in RTL)."""
    return page.evaluate(
        """() => {
          const el = document.querySelector('.switch[aria-checked="true"]');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el, '::after');
          const w = parseFloat(cs.width) || 16;
          const m = new DOMMatrixReadOnly(
            cs.transform === 'none' ? 'matrix(1,0,0,1,0,0)' : cs.transform);
          let leftPx = parseFloat(cs.left);
          if (Number.isNaN(leftPx)) {
            const rt = parseFloat(cs.right);
            leftPx = Number.isNaN(rt) ? 2 : r.width - rt - w;
          }
          return {
            bg: getComputedStyle(el).backgroundColor,
            knob: cs.backgroundColor,
            tx: m.e,
            cx: (leftPx + w / 2 + m.e) / r.width,
          };
        }"""
    )


def region_fields(page) -> tuple[float, float]:
    vals = page.evaluate(
        """() => [...document.querySelectorAll(
            '.preview-fields input.num-field')].map(i => parseFloat(i.value))"""
    )
    return (float(vals[0]), float(vals[1])) if len(vals) == 2 else (0.0, 0.0)


def check_progress_samples(samples: list) -> None:
    """overall_pct fill: 0<w≤100, non-decreasing, and ahead of (n-1)/N."""
    widths = [s["w"] for s in samples if s["w"] > 0]
    check("progress: fill observed during run", len(widths) >= 1,
          f"{len(samples)} samples")
    if len(widths) >= 2:
        check("progress: fill width monotonic",
              all(b >= a - 0.5 for a, b in zip(widths, widths[1:])),
          str(widths[:8]))
    for s in samples:
        m = re.search(r"(\d+)\s*of\s*(\d+)", s["meta"])
        if m:
            n, total = int(m.group(1)), int(m.group(2))
            floor = (n - 1) / max(total, 1) * 100
            check(f"progress: step {n}/{total} fill ≥ (n-1)/N",
                  s["w"] >= floor - 1,
                  f"fill={s['w']:.1f}% floor={floor:.0f}%")
            break  # one observed step is enough


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ss-e2e-"))
    ar_name = tmp / "تسجيل عربي.mp4"
    shutil.copyfile(FIX / "clip.mp4", ar_name)

    if SHOTS.exists():
        for f in SHOTS.glob("*.png"):
            f.unlink()

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
    try:
        wait_health(server)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ---------- pass 1: EN @1440 — well, region, video, progress --
            print("== EN 1440: upload → well → region → video ==")
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(BASE)
            page.wait_for_selector(".drop-title", timeout=20000)

            upload_file(page, ar_name)
            page.wait_for_selector(".inspector", timeout=60000)

            # --- waveform well ------------------------------------------------
            page.wait_for_selector(".well .wave-surface canvas",
                                   timeout=30000)
            check("well: wavesurfer rendered (canvas present)",
                  page.locator(".well .wave-surface canvas").count() >= 1)
            well_dir = page.locator(".well").get_attribute("dir")
            check("well: dir=ltr island in EN", well_dir == "ltr", str(well_dir))
            check("well: ruler ticks drawn",
                  page.locator(".ruler-tick").count() >= 3,
                  str(page.locator(".ruler-tick").count()))
            check("well: region + two handles shown",
                  page.locator(".preview-region").count() == 1
                  and page.locator(".ph").count() == 2)
            check("well: zoom buttons labelled",
                  page.locator('button[aria-label*="waveform"]').count() >= 2)

            # --- video well (clip.mp4 has a video stream) --------------------
            page.wait_for_selector(".video-well video", timeout=15000)
            n_vid = page.locator(".video-well .compare-video").count()
            check("video: well rendered for clip.mp4", n_vid >= 1,
                  f"{n_vid} video elements")
            vstate = page.evaluate(
                """() => {
                  const v = document.querySelector('.compare-video');
                  return v ? {muted: v.muted,
                              ready: v.readyState} : null;
                }""")
            check("video: muted display mirror", bool(vstate) and vstate["muted"],
                  str(vstate))

            # --- ON-switch parity baseline (EN) ------------------------------
            page.locator('[data-op-id="loudness"] .switch').click()
            page.wait_for_timeout(400)
            sw_en = switch_probe(page)
            check("switch EN: ON = white track + dark knob",
                  bool(sw_en) and sw_en["bg"] == "rgb(255, 255, 255)"
                  and sw_en["knob"] == "rgb(0, 0, 0)",
                  str(sw_en))
            check("switch EN: knob at inline-end (right half)",
                  bool(sw_en) and sw_en["cx"] > 0.6,
                  f"cx={sw_en and round(sw_en['cx'], 3)}")

            # --- region drag ↔ numeric sync -----------------------------------
            s0, e0 = region_fields(page)
            check("region: fields sane at start", s0 == 0 and e0 > 0,
                  f"{s0}..{e0}")
            dur_s = page.evaluate(
                "document.querySelector('audio').duration")
            stack_w = page.locator(".well-stack").bounding_box()["width"]
            pps = stack_w / float(dur_s)

            def drag_handle(sel: str, seconds: float):
                box = page.locator(sel).bounding_box()
                dx = pps * seconds
                page.mouse.move(box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] / 2 + dx,
                                box["y"] + box["height"] / 2, steps=6)
                page.mouse.up()
                page.wait_for_timeout(200)

            # Handles at the stack edges are half-clipped; pull the region
            # inside first, then resize it by its handles.
            f_reg = page.locator(".preview-fields input.num-field")
            f_reg.nth(0).fill("2")
            f_reg.nth(1).fill("8")
            page.evaluate("document.activeElement.blur()")
            page.wait_for_timeout(200)
            s0, e0 = region_fields(page)
            drag_handle(".ph-start", -1.0)
            s1, e1 = region_fields(page)
            check("region: drag start handle → start field follows",
                  abs(s1 - s0 + 1.0) < 0.4 and abs(e1 - e0) < 0.01,
                  f"start {s0}→{s1}, end {e0}→{e1}")
            drag_handle(".ph-end", 1.0)
            s1b, e1b = region_fields(page)
            check("region: drag end handle → end field follows",
                  abs(e1b - e1 - 1.0) < 0.4 and abs(s1b - s1) < 0.01,
                  f"start {s1}→{s1b}, end {e1}→{e1b}")

            # --- [ / ] nudge 0.5 s -------------------------------------------
            page.keyboard.press("BracketRight")
            page.wait_for_timeout(150)
            s2, e2 = region_fields(page)
            check("region: ] nudges +0.5 s",
                  abs(s2 - s1b - 0.5) < 0.05 and abs(e2 - e1b - 0.5) < 0.05,
                  f"{s1b}..{e1b} → {s2}..{e2}")
            page.keyboard.press("BracketLeft")
            page.wait_for_timeout(150)
            s3, e3 = region_fields(page)
            check("region: [ nudges back −0.5 s",
                  abs(s3 - s1b) < 0.05 and abs(e3 - e1b) < 0.05,
                  f"{s2}..{e2} → {s3}..{e3}")

            # --- event.code shortcuts (EN layout) -----------------------------
            # Focus may still sit on an op switch (Space toggles a focused
            # switch — correct); blur so Space reaches the transport.
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_timeout(400)
            check("kbd: Space plays",
                  page.evaluate(
                      "![...document.querySelectorAll('audio')]"
                      ".every(a => a.paused)"))
            page.evaluate(
                "document.querySelector('audio').currentTime = 1.0")
            page.wait_for_timeout(150)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            t_a = page.evaluate("document.querySelector('audio').currentTime")
            check("kbd: → seeks +5 s", abs(t_a - 6.0) < 0.4, f"{t_a:.2f}")
            page.keyboard.press("Space")
            page.wait_for_timeout(150)

            # P: play just the region — set a small 1..3 s region, then
            # blur the field (shortcuts are ignored while typing).
            fields = page.locator(".preview-fields input.num-field")
            fields.nth(0).fill("1")
            fields.nth(1).fill("3")
            page.evaluate("document.activeElement.blur()")
            page.wait_for_timeout(150)
            page.keyboard.press("KeyP")
            page.wait_for_timeout(250)
            rp = page.evaluate("document.querySelector('audio').currentTime")
            check("kbd: P starts at region start", abs(rp - 1.0) < 0.5,
                  f"{rp:.2f}")
            check("kbd: P region play is playing",
                  page.evaluate("!document.querySelector('audio').paused"))
            page.wait_for_timeout(2600)
            p_state = page.evaluate(
                """() => ({t: document.querySelector('audio').currentTime,
                          p: document.querySelector('audio').paused})""")
            check("kbd: region play stops at region end",
                  p_state["p"] and abs(p_state["t"] - 3.0) < 0.6,
                  str(p_state))

            # --- video mirror: seek while paused syncs the frame --------------
            page.evaluate(
                "document.querySelector('audio').currentTime = 5.0")
            page.wait_for_timeout(400)
            vt = page.evaluate(
                "document.querySelector('.compare-video').currentTime")
            check("video: frame follows the playhead",
                  abs(vt - 5.0) < 0.6, f"video={vt:.2f}")
            shot(page, "en-1440-well.png")

            # --- full job → processed video joins the A/B --------------------
            page.get_by_role("button", name="Process", exact=True).click()
            fsamples: list = []
            fjob = wait_job(page, "full", sample_progress=fsamples)
            check("full: job completed", fjob["state"] == "completed",
                  f"state={fjob['state']}")
            check_progress_samples(fsamples)
            page.wait_for_selector(".transport .seg", timeout=30000)
            nv = page.locator(".video-well .compare-video").count()
            check("video: processed frame joins the A/B", nv == 2, str(nv))
            page.locator(".transport .seg-opt").nth(1).click()
            page.wait_for_timeout(700)
            vis = page.evaluate(
                """() => [...document.querySelectorAll('.compare-video')]
                    .map(v => ({hidden: v.classList.contains('behind'),
                                t: v.currentTime}))""")
            check("video: B side shows the processed frame",
                  len(vis) == 2 and vis[0]["hidden"] and not vis[1]["hidden"],
                  str(vis))
            bt = page.evaluate(
                "document.querySelectorAll('audio')[1].currentTime")
            check("video: processed frame mirrors the B playhead",
                  len(vis) == 2 and abs(vis[1]["t"] - bt) < 0.6,
                  f"vb={vis and vis[1]['t']:.2f} b={bt:.2f}")
            shot(page, "en-1440-video-ab.png")

            # ---------- silence preview → cuts → A/B jump (offset) -----------
            print("== EN 1440: silence preview → hatches → A/B cut-jump ==")
            ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
            page2 = ctx2.new_page()
            page2.goto(BASE)
            page2.wait_for_selector(".drop-title, .inspector", timeout=20000)
            upload_file(page2, FIX / "speech_silences.wav")
            page2.wait_for_selector(".inspector", timeout=60000)
            page2.wait_for_selector(".well .wave-surface canvas",
                                    timeout=30000)

            page2.locator('[data-op-id="silence"] .switch').click()
            page2.wait_for_timeout(900)

            # region 3..21: non-zero start → preview timeline is offset.
            f2 = page2.locator(".preview-fields input.num-field")
            f2.nth(0).fill("3")
            f2.nth(1).fill("21")
            page2.wait_for_timeout(150)

            samples: list = []
            page2.locator(".preview-fields .btn-secondary").click()
            pjob = wait_job(page2, "preview", sample_progress=samples)
            check("preview: job completed", pjob["state"] == "completed",
                  f"state={pjob['state']}")
            check_progress_samples(samples)

            # Processed wave mounts → amber layer behind the grey one.
            page2.wait_for_selector(".wave-layer.proc .wave-surface canvas",
                                    timeout=30000)
            check("well: processed wave rendered after preview",
                  page2.locator(".wave-layer.proc canvas").count() >= 1)

            tl = page2.request.get(
                f"{BASE}/api/jobs/{pjob['id']}/timeline").json()
            src_off = (pjob.get("preview") or {}).get("start_s") or 0.0
            removed = tl.get("removed") or []
            check("timeline: preview has removed spans", len(removed) >= 1,
                  f"{len(removed)} spans")
            n_hatch = page2.locator(".cut-span").count()
            check("well: hatch spans drawn on original wave",
                  n_hatch == len(removed),
                  f"{n_hatch} hatches vs {len(removed)} removed")
            check("well: hatch uses 45° pattern (not plain colour)",
                  "gradient" in (page2.locator(".cut-span").first.evaluate(
                      "el => getComputedStyle(el).backgroundImage") or ""),
                  page2.locator(".cut-span").first.evaluate(
                      "el => getComputedStyle(el).backgroundImage")[:60]
                  if n_hatch else "none")
            check("cutlist: textual removed list",
                  page2.locator(".cut-list li").count() == len(removed),
                  f"{page2.locator('.cut-list li').count()} rows")
            if n_hatch:
                page2.locator(".cut-span").first.hover()
                lbl = page2.locator(".cut-label").first
                check("well: hover label 'Removed …' on hatch",
                      "Removed" in lbl.inner_text(), lbl.inner_text())
            shot(page2, "en-1440-cuts.png")

            # A/B jump across a cut, through the preview source offset:
            # aEl time is absolute; the preview timeline is region-local.
            if removed:
                a, b = removed[0]
                mid_local = (a + b) / 2
                mid_abs = src_off + mid_local
                nks = next_kept_src(tl, mid_local)
                expected = src_to_out(tl, nks) if nks is not None else 0.0
                page2.evaluate(
                    "document.querySelectorAll('audio')[0].currentTime = "
                    + repr(float(mid_abs)))
                page2.locator(".transport .seg-opt").nth(1).click()
                page2.wait_for_timeout(600)
                got = page2.evaluate(
                    "document.querySelectorAll('audio')[1].currentTime")
                check("ab: switch inside cut jumps to next kept point",
                      abs(got - expected) < 0.35,
                      f"mid_abs={mid_abs:.2f} exp≈{expected:.2f} got={got:.2f}")
                proc_front = page2.evaluate(
                    """() => !document.querySelector('.wave-layer.proc')
                        .classList.contains('behind')""")
                check("ab: processed wave comes in front on B",
                      proc_front)
                # B side wave is amber via --data token.
                amber = page2.evaluate(
                    """() => getComputedStyle(document.documentElement)
                        .getPropertyValue('--data').trim()""")
                check("well: --data amber token used for processed wave",
                      amber == "#f2b33d", amber)
                # …and it actually PAINTS amber/grey, not wavesurfer's #999
                # fallback (canvas can't resolve var() — we resolve first).
                def canvas_probe(sel):
                    # wavesurfer draws inside an OPEN shadow root.
                    return page2.evaluate(
                        """sel => {
                          const layer = document.querySelector(sel);
                          if (!layer) return null;
                          const host = [...layer.querySelectorAll('*')]
                            .find(e => e.shadowRoot);
                          const c = host && host.shadowRoot.querySelector('canvas');
                          if (!c) return null;
                          const x = c.getContext('2d');
                          const d = x.getImageData(0, 0, c.width, c.height).data;
                          let amber = 0, grey = 0;
                          for (let i = 0; i < d.length; i += 4) {
                            const r = d[i], g = d[i+1], b = d[i+2], a = d[i+3];
                            if (a <= 100) continue;
                            if (r > 170 && g > 110 && g < 200 && b < 120)
                              amber++;
                            if (Math.abs(r - g) < 25 && Math.abs(g - b) < 25
                                && r > 70 && r < 160)
                              grey++;
                          }
                          return {amber, grey};
                        }""", sel)
                proc_px = canvas_probe(".wave-layer.proc")
                orig_px = canvas_probe(".wave-layer.orig")
                check("well: processed wave PAINTED amber on canvas",
                      bool(proc_px) and proc_px["amber"] > 50, str(proc_px))
                check("well: original wave PAINTED grey on canvas",
                      bool(orig_px) and orig_px["grey"] > 50, str(orig_px))
            shot(page2, "en-1440-ab.png")

            # ---------- AR pass on the same loaded page -----------------------
            print("== AR 1440: RTL geometry + switch parity ==")
            page2.locator(".lang-btn").click()
            page2.wait_for_function("document.documentElement.dir === 'rtl'")
            page2.wait_for_timeout(800)
            check("ar: dir=rtl applied", True)
            well_dir_ar = page2.locator(".well").get_attribute("dir")
            check("ar: well stays dir=ltr", well_dir_ar == "ltr",
                  str(well_dir_ar))
            order = page2.evaluate(
                """() => {
                  const bx = (el) => el && el.getBoundingClientRect().x;
                  const play = bx(document.querySelector('.play-btn'));
                  const bar = bx(document.querySelector('.playback'));
                  const tcs = [...document
                    .querySelectorAll('.transport-island .tc')]
                    .map(e => e.getBoundingClientRect().x);
                  return {play, bar, dur: tcs[1]};
                }""")
            check("ar: transport island not mirrored",
                  order["play"] is not None and order["bar"] is not None
                  and order["dur"] is not None
                  and order["play"] < order["bar"] < order["dur"],
                  str(order))
            lab_dir = page2.locator(".cut-label").first.get_attribute("dir") \
                if page2.locator(".cut-label").count() else None
            check("ar: cut labels are dir=rtl spans", lab_dir == "rtl",
                  str(lab_dir))
            # ruler ticks + region stay in px space — no mirror.
            tick0 = page2.locator(".ruler-tick").first.bounding_box()
            stack = page2.locator(".well-stack").bounding_box()
            check("ar: ruler ticks still LTR-anchored",
                  tick0 and stack and
                  abs(tick0["x"] - stack["x"]) < 30,
                  f"tick0.x={tick0 and tick0['x']} stack.x={stack and stack['x']}")
            sw_ar = switch_probe(page2)
            check("switch AR: ON = white track + dark knob (same as EN)",
                  bool(sw_ar) and sw_ar["bg"] == "rgb(255, 255, 255)"
                  and sw_ar["knob"] == "rgb(0, 0, 0)",
                  str(sw_ar))
            check("switch AR: knob mirrored to inline-end (left half)",
                  bool(sw_ar) and sw_ar["cx"] < 0.4,
                  f"cx={sw_ar and round(sw_ar['cx'], 3)}")
            check("switch parity: EN+AR knob centres sum ≈1 (mirror)",
                  bool(sw_en) and bool(sw_ar)
                  and abs(sw_en["cx"] + sw_ar["cx"] - 1.0) < 0.12,
                  f"en={sw_en and round(sw_en['cx'],3)} "
                  f"ar={sw_ar and round(sw_ar['cx'],3)}")
            check("ar: no h-overflow @1440", no_overflow(page2))
            shot(page2, "ar-1440-well.png")

            # AR keyboard: event.code shortcuts still fire on the Arabic
            # layout (physical keys — the browser reports real codes).
            # seekBy acts on the ACTIVE side — the A/B test left B selected,
            # so return to A first.
            page2.keyboard.press("KeyA")
            page2.wait_for_timeout(200)
            page2.evaluate(
                "document.querySelector('audio').currentTime = 1.0")
            page2.keyboard.press("ArrowRight")
            page2.wait_for_timeout(150)
            t_ar = page2.evaluate("document.querySelector('audio').currentTime")
            check("ar kbd: → seeks +5 s (event.code)",
                  abs(t_ar - 6.0) < 0.4, f"{t_ar:.2f}")
            page2.keyboard.press("KeyB")
            page2.wait_for_timeout(300)
            b_checked = page2.evaluate(
                "[...document.querySelectorAll('.transport .seg-opt')]"
                ".find(b => b.getAttribute('aria-checked') === 'true')"
                "?.textContent")
            check("ar kbd: KeyB selects Processed (event.code)",
                  b_checked == "بعد المعالجة", str(b_checked))
            page2.keyboard.press("KeyA")
            page2.wait_for_timeout(200)

            # ---------- width sweep EN+AR -------------------------------------
            print("== width sweep 800/390 EN+AR ==")
            for w, h, tag in ((800, 900, "800"), (390, 844, "390")):
                page2.set_viewport_size({"width": w, "height": h})
                page2.wait_for_timeout(600)
                check(f"ar: no h-overflow @{tag}", no_overflow(page2))
                if w <= 800:
                    cm = page2.locator(".compare-media").evaluate(
                        "el => getComputedStyle(el).flexDirection")
                    check(f"ar: compare-media stacks @{tag}",
                          cm == "column", cm)
                shot(page2, f"ar-{tag}-well.png")
            page2.locator(".lang-btn").click()
            page2.wait_for_function("document.documentElement.dir === 'ltr'")
            for w, h, tag in ((800, 900, "800"), (390, 844, "390")):
                page2.set_viewport_size({"width": w, "height": h})
                page2.wait_for_timeout(600)
                check(f"en: no h-overflow @{tag}", no_overflow(page2))
                check(f"en: well visible @{tag}",
                      page2.locator(".well").is_visible())
                shot(page2, f"en-{tag}-well.png")
            ctx2.close()
            ctx.close()
            browser.close()
    finally:
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
