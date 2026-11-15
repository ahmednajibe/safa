import { useRef } from "react";
import { removeMedia, startUpload } from "../actions";
import type { AudioStreamInfo } from "../api/types";
import { useT } from "../i18n";
import { fmtBytes, fmtDuration } from "../lib/format";
import { useStore } from "../state/store";

const ACCEPT =
  "audio/*,video/*,.wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.mp4,.mkv,.mov,.webm,.avi";

function chanLabel(s: AudioStreamInfo, t: ReturnType<typeof useT>): string {
  const layout = s.channel_layout?.toLowerCase() ?? "";
  if (s.channels === 2 || layout.includes("stereo")) return t.d.inspector.stereo;
  if (s.channels === 1 || layout.includes("mono")) return t.d.inspector.mono;
  return t.t(t.d.inspector.channelsN, { n: t.num(s.channels) });
}

/** Fact card for the loaded file: duration/format/size, stream chips, LUFS,
 *  audio-track picker for multi-track files, Replace/Remove. */
export function MediaInspector() {
  const t = useT();
  const store = useStore();
  const { media } = store;
  const inputRef = useRef<HTMLInputElement>(null);

  const probe = media.probe;
  const streams = probe?.audio ?? [];
  const video = probe?.video?.[0];
  const selStream =
    media.audioStream !== null && streams.length > 1
      ? media.audioStream
      : (streams[0]?.index ?? null);
  const cur = streams.find((s) => s.index === selStream) ?? streams[0];

  return (
    <section className="inspector" aria-label={media.filename ?? ""}>
      <div className="insp-head">
        <div className="insp-name">
          <bdi>{media.filename}</bdi>
        </div>
        <button
          type="button"
          className="link-btn"
          onClick={() => inputRef.current?.click()}
        >
          {t.d.inspector.replace}
        </button>
        <button
          type="button"
          className="link-btn neg"
          onClick={() => removeMedia(store)}
        >
          {t.d.drop.remove}
        </button>
      </div>

      <div className="chips">
        {probe && (
          <span className="chip mono" dir="ltr">
            {fmtDuration(probe.duration_s)}
          </span>
        )}
        {probe && (
          <span className="chip" dir="ltr">
            {probe.format_name.toUpperCase().split(",")[0]}
          </span>
        )}
        <span className="chip" dir="ltr">
          {fmtBytes(media.sizeBytes)}
        </span>
        {cur && (
          <span className="chip">
            {cur.codec.toUpperCase()} · {chanLabel(cur, t)} ·{" "}
            {t.t(t.d.inspector.khz, { n: t.num(cur.sample_rate / 1000) })}
          </span>
        )}
        <span className="chip">
          {media.lufs !== null
            ? t.t(t.d.inspector.lufs, { n: t.num(media.lufs) })
            : t.d.inspector.notMeasured}
        </span>
        {video && (
          <span className="chip mono" dir="ltr">
            {t.t(t.d.inspector.videoTag, {
              w: t.num(video.width, { useGrouping: false }),
              h: t.num(video.height, { useGrouping: false }),
            })}
          </span>
        )}
      </div>

      {streams.length > 1 && (
        <label className="stream-row">
          <span>{t.d.inspector.track}</span>
          <select
            className="select"
            value={selStream ?? 0}
            onChange={(e) =>
              store.dispatchMedia({
                type: "set-stream",
                index: Number(e.target.value),
              })
            }
          >
            {streams.map((s, i) => (
              <option key={s.index} value={s.index}>
                {t.t(t.d.inspector.trackN, { n: t.num(i + 1) })} —{" "}
                {s.codec.toUpperCase()} · {chanLabel(s, t)}
              </option>
            ))}
          </select>
        </label>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) startUpload(store, f);
          e.target.value = "";
        }}
      />
    </section>
  );
}
