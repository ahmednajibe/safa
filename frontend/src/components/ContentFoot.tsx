import type { Estimate } from "../api/types";
import { useT } from "../i18n";
import { isTerminal } from "../state/reducers";
import { useStore } from "../state/store";
import { dockSubline, JobResult } from "./Dock";
import { Switch } from "./Switch";

/** Content foot (≤600 px): the keep-video switch, the estimate subline and
 *  the done/save actions that would crowd the compact dock live here, in
 *  the content column directly above the dock. Renders nothing on wide
 *  screens (the dock shows them there instead). */
export function ContentFoot({
  estimate,
  gpuName,
  mediaReady,
  hasOps,
}: {
  estimate: Estimate | null;
  gpuName: string | null;
  mediaReady: boolean;
  hasOps: boolean;
}) {
  const t = useT();
  const store = useStore();
  const { media, ui, job } = store;
  const isVideo = media.probe?.has_video === true;
  const showResult = !!job.job && isTerminal(job.job);

  return (
    <div className="content-foot">
      <div className="foot-opts">
        {isVideo && (
          <label className="kv-row">
            <Switch
              checked={ui.keepVideo}
              onChange={(v) =>
                store.dispatchUi({ type: "output", keepVideo: v })
              }
              label={t.d.output.keepVideo}
            />
            <span>{t.d.output.keepVideo}</span>
          </label>
        )}
        <span className="estimate-sub">
          {dockSubline(t, {
            mediaReady,
            hasOps,
            mediaStatus: media.status,
            estimate,
            device: ui.device,
            gpuName,
          })}
        </span>
      </div>
      {showResult && <JobResult />}
    </div>
  );
}
