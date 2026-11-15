import { useState, type FormEvent } from "react";
import {
  deleteOutputDir,
  pickOutputDir,
  putOutputDir,
  revealOutputDir,
} from "../api/client";
import { ApiError } from "../api/types";
import { broadcastSettings } from "../hooks/useSync";
import { useT } from "../i18n";
import { truncateMiddle } from "../lib/format";
import { useStore } from "../state/store";

/** "Save results" popover: Ask every time ⇄ automatic folder (native picker
 *  or pasted path). While the backend's tkinter dialog is up, the UI shows
 *  the "window that opened" hint until the POST resolves. */
export function SettingsPopover() {
  const t = useT();
  const { settings, dispatchSettings } = useStore();
  const [picking, setPicking] = useState(false);
  const [pathMode, setPathMode] = useState(false);
  const [pathInput, setPathInput] = useState("");
  const [errCode, setErrCode] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  const setDir = (dir: string | null) => {
    dispatchSettings({ type: "settings", outputDir: dir });
    broadcastSettings(dir);
  };

  const chooseAsk = () => {
    deleteOutputDir()
      .then(() => setDir(null))
      .catch(() => {});
    setErrCode(null);
    setSavedNotice(null);
    setPathMode(false);
  };

  const startPick = () => {
    setPicking(true);
    setErrCode(null);
    setSavedNotice(null);
    pickOutputDir()
      .then((r) => {
        if (!r.cancelled && r.output_dir) {
          setDir(r.output_dir);
          setSavedNotice(r.output_dir);
        }
      })
      .catch((e: unknown) => {
        setErrCode(e instanceof ApiError ? e.code : "picker_unavailable");
      })
      .finally(() => setPicking(false));
  };

  const submitPath = (e: FormEvent) => {
    e.preventDefault();
    const p = pathInput.trim();
    if (!p) return;
    setErrCode(null);
    putOutputDir(p)
      .then((r) => {
        setDir(r.output_dir);
        setSavedNotice(r.output_dir);
        setPathMode(false);
        setPathInput("");
      })
      .catch((e2: unknown) => {
        setErrCode(e2 instanceof ApiError ? e2.code : "output_dir_invalid");
      });
  };

  const auto = settings.outputDir !== null;

  return (
    <div className="popover settings-pop" role="dialog" aria-label={t.d.settings.title}>
      <div className="pop-title">{t.d.settings.title}</div>

      <label className="radio-row">
        <input
          type="radio"
          name="save-mode"
          checked={!auto}
          onChange={chooseAsk}
        />
        <span>{t.d.settings.ask}</span>
      </label>

      <label className="radio-row">
        <input
          type="radio"
          name="save-mode"
          checked={auto}
          onChange={() => {
            if (!auto) startPick();
          }}
        />
        <span>{t.d.settings.auto}</span>
      </label>

      {auto && (
        <div className="auto-block">
          <div className="path-line">
            <bdi>{truncateMiddle(settings.outputDir ?? "", 46)}</bdi>
          </div>
          <div className="pop-actions">
            <button
              type="button"
              className="btn-small"
              onClick={startPick}
              disabled={picking}
            >
              {t.d.settings.change}
            </button>
            <button
              type="button"
              className="btn-small"
              onClick={() =>
                revealOutputDir().catch(() => setErrCode("no_saved_path"))
              }
            >
              {t.d.settings.showInFolder}
            </button>
          </div>
        </div>
      )}

      {picking && <div className="pop-note">{t.d.settings.picking}</div>}
      {savedNotice && !errCode && (
        <div className="pop-note">
          {t.msg("output_dir_set", { path: savedNotice })}
        </div>
      )}
      {errCode && (
        <div className="pop-note neg" role="alert">
          {t.msg(errCode, {}, t.d.settings.invalid)}
        </div>
      )}

      {!pathMode ? (
        <button
          type="button"
          className="link-btn"
          onClick={() => setPathMode(true)}
        >
          {t.d.settings.typePath}
        </button>
      ) : (
        <form className="path-form" onSubmit={submitPath}>
          <input
            type="text"
            className="path-input"
            dir="ltr"
            placeholder={t.d.settings.pathPlaceholder}
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            aria-label={t.d.settings.savePath}
          />
          <button type="submit" className="btn-small">
            {t.d.action.save}
          </button>
        </form>
      )}
    </div>
  );
}
