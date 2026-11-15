/** Display formatting: timecodes, bytes, durations, paths.
 *  All numeric output uses Western digits (ar-u-nu-latn) + tabular class. */

export function nf(lang: string): Intl.NumberFormat {
  return new Intl.NumberFormat(lang === "ar" ? "ar-u-nu-latn" : "en-US", {
    maximumFractionDigits: 1,
  });
}

/** "42:18" / "1:02:03" — seconds → h?:mm:ss */
export function fmtDuration(s: number): string {
  const t = Math.max(0, s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = Math.floor(t % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Timecode "00:12.40" — mm:ss.cc for the transport. */
export function fmtTimecode(s: number): string {
  const t = Math.max(0, s);
  const m = Math.floor(t / 60);
  const sec = Math.floor(t % 60);
  const cs = Math.floor((t * 100) % 100);
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

/** "Removed 0:03.2"-style short span: m:ss.d */
export function fmtSpan(s: number): string {
  const t = Math.max(0, s);
  const m = Math.floor(t / 60);
  const sec = t % 60;
  const whole = Math.floor(sec);
  const tenth = Math.floor((sec - whole) * 10);
  return `${m}:${String(whole).padStart(2, "0")}.${tenth}`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${Math.round(n)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const rounded = v >= 100 ? Math.round(v) : Math.round(v * 10) / 10;
  return `${rounded} ${units[i]}`;
}

/** ETA seconds → "4 min" / "35 s" (coarse). Caller wraps in "About {t} left". */
export function fmtEta(sec: number): string | null {
  if (!Number.isFinite(sec) || sec < 0) return null;
  if (sec < 60) return null; // under a minute → etaShort
  const min = Math.round(sec / 60);
  return `${min} min`;
}

/** Middle-truncate a filesystem path for display. */
export function truncateMiddle(s: string, max = 42): string {
  if (s.length <= max) return s;
  const keep = Math.max(4, Math.floor((max - 1) / 2));
  return `${s.slice(0, keep)}…${s.slice(-keep)}`;
}

export function shortGpuName(name: string): string {
  return name.replace(/^NVIDIA\s+(GeForce\s+)?/i, "").trim() || name;
}
