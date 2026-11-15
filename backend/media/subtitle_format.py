"""Subtitle cue builder + SRT/VTT writers (M8 Task 2, argon_m8 Q3).

Turns word-timed transcript segments into display cues:

- Breaks at utterance (segment) boundaries, ≥ ``_BREAK_GAP`` s pauses,
  the 7 s cue cap and the line budget — never inside a word.
- Lines hold ≤ ``max_line_chars`` *visible* chars; a cue holds ≤
  ``max_lines`` lines. Any line containing Arabic (U+0600–U+06FF) is
  prefixed with U+200F (RLM) which does not count toward the limit.
- Cue durations aim for 1–7 s: short cues first merge with or take whole
  lines from the previous cue (subject to the line budget and 7 s cap),
  then extend into the following pause, then (bounded) push the next
  cue's start later. The ≥80 ms gap between cues is the hard constraint
  and always wins.
- Orphan punctuation tokens (``، ؛ ؟ »`` et al.) merge with the
  neighbouring word — a safety net under faster-whisper's own
  ``merge_punctuations``.

Writers: SRT as ``utf-8-sig`` (BOM — Windows player compatibility), VTT
as ``utf-8`` with a ``WEBVTT`` header and no BOM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

RLM = "‏"  # RIGHT-TO-LEFT MARK
_ARABIC_RE = re.compile(r"[؀-ۿ]")

MIN_DUR_S = 1.0
MAX_DUR_S = 7.0
CUE_GAP_S = 0.08
_BREAK_GAP_S = 0.6     # a pause this long always splits cues
_MAX_SHIFT_S = 0.4     # how late a pushed cue may appear vs its words
_MIN_FLASH_S = 0.35    # floor when pushing a cue's start later

_TRAIL_PUNCT = set(".,!?:;،؛؟»…')]}”")
_LEAD_PUNCT = set("«\"'“¿([{-")

_EPS = 1e-9


def visible_len(line: str) -> int:
    """Char count a viewer sees — RLM marks don't count."""
    return len(line.replace(RLM, ""))


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]


@dataclass
class _W:
    start: float
    end: float
    text: str


@dataclass
class _Packed:
    start: float
    end: float                      # last word's end (display end refined later)
    lines: list[list[_W]] = field(default_factory=list)

    @property
    def words(self) -> list[_W]:
        return [w for line in self.lines for w in line]

    @property
    def first_word_start(self) -> float:
        return self.lines[0][0].start

    def refresh(self) -> None:
        """Re-sync start/end after line surgery."""
        self.start = self.lines[0][0].start
        self.end = self.lines[-1][-1].end


def _merge_stray_punct(words: list[_W]) -> list[_W]:
    """Fold pure-punctuation tokens into their neighbouring words."""
    out: list[_W] = []
    pending: list[_W] = []
    for w in words:
        t = w.text.strip()
        if not t:
            continue
        if all(ch in _TRAIL_PUNCT for ch in t):
            if out:
                out[-1].text += t
                out[-1].end = max(out[-1].end, w.end)
            else:
                pending.append(w)
        elif all(ch in _LEAD_PUNCT for ch in t):
            pending.append(w)
        else:
            if pending:
                w.text = "".join(p.text.strip() for p in pending) + t
                w.start = min(w.start, pending[0].start)
                pending = []
            out.append(w)
    for p in pending:  # dangling lead-punct at stream end → attach back
        if out:
            out[-1].text += p.text.strip()
            out[-1].end = max(out[-1].end, p.end)
    return out


def _line_text(words: list[_W]) -> str:
    return " ".join(w.text.strip() for w in words if w.text.strip())


def _display_lines(packed: _Packed) -> list[str]:
    lines = []
    for words in packed.lines:
        text = _line_text(words)
        if _ARABIC_RE.search(text):
            text = RLM + text
        lines.append(text)
    return lines


def _pack(
    segments: Iterable,
    max_line_chars: int,
    max_lines: int,
) -> list[_Packed]:
    """Greedy word→line→cue packing honouring every hard limit."""
    cues: list[_Packed] = []
    cur: _Packed | None = None
    cur_lines: list[list[_W]] = []
    prev_end: float | None = None

    def close() -> None:
        nonlocal cur, cur_lines
        if cur is not None:
            cur.lines = cur_lines
            cues.append(cur)
        cur = None
        cur_lines = []

    def fresh(w: _W) -> None:
        nonlocal cur, cur_lines
        cur = _Packed(w.start, w.end)
        cur_lines = [[]]

    for seg in segments:
        words = _merge_stray_punct(
            [_W(float(w.start), float(w.end), str(w.text))
             for w in seg.words]
        )
        for wi, w in enumerate(words):
            if cur is None:
                fresh(w)
            elif (
                wi == 0                                  # utterance edge
                or w.end - cur.start > MAX_DUR_S         # 7 s cap
                or (prev_end is not None
                    and w.start - prev_end >= _BREAK_GAP_S)
            ):
                close()
                fresh(w)
            line = cur_lines[-1]
            candidate = (
                w.text.strip()
                if not line
                else _line_text(line) + " " + w.text.strip()
            )
            if len(candidate) <= max_line_chars or not line:
                # a word wider than the limit keeps its own line whole —
                # words are never split
                line.append(w)
            elif len(cur_lines) < max_lines:
                cur_lines.append([w])
            else:
                close()
                fresh(w)
                cur_lines[-1].append(w)
            cur.end = w.end
            prev_end = w.end
    close()
    return cues


def _rebalance(cues: list[_Packed], max_lines: int) -> None:
    """Pull a too-short cue's content from the previous cue — whole lines
    only (never split a word), only inside the line budget and 7 s cap."""
    i = 1
    while i < len(cues):
        c = cues[i]
        if c.end - c.start >= MIN_DUR_S:
            i += 1
            continue
        prev = cues[i - 1]
        if (
            len(c.lines) + len(prev.lines) <= max_lines
            and c.end - prev.start <= MAX_DUR_S
        ):
            # full merge: prev's words move into c wholesale
            c.lines = prev.lines + c.lines
            c.refresh()
            del cues[i - 1]
            i -= 1  # c slid into prev's slot — re-check it
            continue
        if len(c.lines) < max_lines and prev.lines:
            line = prev.lines.pop()
            c.lines.insert(0, line)
            c.refresh()
            if prev.lines:
                prev.refresh()
            else:
                del cues[i - 1]
                i -= 1
            continue
        i += 1  # boxed in — stays under 1 s (rare; gap rule still holds)


def _fix_times(cues: list[_Packed], duration: float) -> None:
    """Enforce the ≥80 ms gap, then grow each cue toward 1 s.

    Per cue: extend into the following pause → push the next cue's start
    (≤ _MAX_SHIFT_S relative to its first word) → trim back to the gap as
    a last resort. Overlapping cue *times* can never survive this pass.
    """
    for i, c in enumerate(cues):
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        limit = (nxt.start - CUE_GAP_S) if nxt else max(duration, c.start)
        c.end = min(max(c.end, c.start + MIN_DUR_S), max(limit, c.start))
        if c.end - c.start < MIN_DUR_S - _EPS and nxt is not None:
            # steal display time from the next cue's start, bounded
            room = max(
                0.0,
                nxt.first_word_start + _MAX_SHIFT_S - nxt.start,
            )
            need = (c.start + MIN_DUR_S + CUE_GAP_S) - nxt.start
            if need > 0 and room > 0:
                nxt.start += min(need, room)
                c.end = min(
                    max(c.end, c.start + MIN_DUR_S),
                    nxt.start - CUE_GAP_S,
                )
        # Hard invariants, unconditionally.
        if c.end < c.start + _MIN_FLASH_S:
            c.end = c.start + _MIN_FLASH_S
        if nxt is not None and c.end > nxt.start - CUE_GAP_S:
            bound = nxt.first_word_start + _MAX_SHIFT_S
            nxt.start = min(c.end + CUE_GAP_S, max(bound, nxt.start))
        if nxt is not None and c.end > nxt.start - CUE_GAP_S:
            c.end = max(nxt.start - CUE_GAP_S, c.start + 0.05)


def build_cues(
    segments: Iterable,
    duration_s: float,
    max_line_chars: int = 42,
    max_lines: int = 2,
) -> list[Cue]:
    """Word-timed segments → display cues under the plan's cue rules."""
    packed = _pack(segments, max_line_chars, max_lines)
    _rebalance(packed, max_lines)
    _fix_times(packed, max(duration_s, 0.0))
    out = []
    for p in packed:
        end = min(p.end, max(duration_s, p.start + 0.05))
        out.append(Cue(p.start, max(end, p.start + 0.05),
                       _display_lines(p)))
    return out


# ---- writers --------------------------------------------------------------------


def _ts(t: float, sep: str) -> str:
    ms = max(0, int(round(t * 1000.0)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(cues: list[Cue], path: Path) -> Path:
    """SRT, ``utf-8-sig`` (BOM) — Windows player compatibility."""
    parts: list[str] = []
    for i, c in enumerate(cues, 1):
        parts.append(
            f"{i}\n{_ts(c.start, ',')} --> {_ts(c.end, ',')}\n"
            + "\n".join(c.lines)
        )
    text = "\n\n".join(parts)
    if text:
        text += "\n"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return Path(path)


def write_vtt(cues: list[Cue], path: Path) -> Path:
    """VTT, plain ``utf-8`` (no BOM) with the WEBVTT header."""
    parts = ["WEBVTT"]
    for i, c in enumerate(cues, 1):
        parts.append(
            f"{i}\n{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n"
            + "\n".join(c.lines)
        )
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n\n".join(parts) + "\n")
    return Path(path)
