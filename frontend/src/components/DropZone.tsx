import { useRef, useState, type DragEvent } from "react";
import { abortUpload, startUpload } from "../actions";
import { useT } from "../i18n";
import { fmtBytes } from "../lib/format";
import { useStore } from "../state/store";

const ACCEPT =
  "audio/*,video/*,.wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.mp4,.mkv,.mov,.webm,.avi";

/** First-run empty state + drag/drop target + the upload/preparing card.
 *  The three states (empty / uploading / preparing) all live in the same
 *  player column so the first action never moves. */
export function DropZone() {
  const t = useT();
  const store = useStore();
  const { media } = store;
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  const onDrag = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      if (e.type === "dragenter") depth.current += 1;
      setOver(true);
    } else if (e.type === "dragleave") {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setOver(false);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    depth.current = 0;
    const f = e.dataTransfer.files?.[0];
    if (f) startUpload(store, f);
  };

  const pick = () => inputRef.current?.click();

  if (media.status === "uploading") {
    const pct =
      media.sizeBytes > 0
        ? Math.min(1, media.uploadedBytes / media.sizeBytes)
        : 0;
    return (
      <section className="drop busy" aria-label={t.d.drop.uploading}>
        <div className="drop-filename">
          <bdi>{media.filename}</bdi>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-valuenow={Math.round(pct * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="progress-fill" style={{ inlineSize: `${pct * 100}%` }} />
        </div>
        <div className="drop-meta">
          {t.d.drop.uploading} {t.num(Math.round(pct * 100))}% ·{" "}
          {fmtBytes(media.uploadedBytes)} / {fmtBytes(media.sizeBytes)}
        </div>
        <button type="button" className="link-btn" onClick={abortUpload}>
          {t.d.action.cancel}
        </button>
      </section>
    );
  }

  if (media.status === "preparing") {
    return (
      <section className="drop busy" aria-label={t.d.drop.preparing}>
        <div className="drop-filename">
          <bdi>{media.filename}</bdi>
        </div>
        <div className="progress progress-indet" role="progressbar">
          <div className="progress-fill" />
        </div>
        <div className="drop-meta">{t.d.drop.preparing}</div>
      </section>
    );
  }

  return (
    <section
      className={`drop${over ? " over" : ""}`}
      onDragEnter={onDrag}
      onDragOver={onDrag}
      onDragLeave={onDrag}
      onDrop={onDrop}
      aria-label={t.d.drop.title}
    >
      <svg
        width="40"
        height="40"
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="drop-icon"
      >
        <path
          d="M12 4v10.2l3.6-3.6L17 12l-5 5-5-5 1.4-1.4L12 14.2V4h2Zm-7 14h14v2H5v-2Z"
          fill="currentColor"
        />
      </svg>
      <h2 className="drop-title">{t.d.drop.title}</h2>
      <button type="button" className="btn-primary" onClick={pick}>
        {t.d.drop.choose}
      </button>
      <p className="drop-meta">{t.d.drop.meta}</p>
      {over && <div className="drop-release">{t.d.drop.release}</div>}
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
