/** App store: one React context holding the five domain states, each with
 *  its own useReducer (media, selection, job, settings, ui). */

import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import {
  emptyJob,
  emptyMedia,
  emptySelection,
  emptySettings,
  emptyUi,
  jobReducer,
  mediaReducer,
  selectionReducer,
  settingsReducer,
  uiReducer,
  type JobAction,
  type JobState,
  type MediaAction,
  type MediaState,
  type SelAction,
  type SelectionState,
  type SettingsAction,
  type SettingsState,
  type UiAction,
  type UiState,
} from "./reducers";

export interface Store {
  media: MediaState;
  selection: SelectionState;
  job: JobState;
  settings: SettingsState;
  ui: UiState;
  dispatchMedia: Dispatch<MediaAction>;
  dispatchSelection: Dispatch<SelAction>;
  dispatchJob: Dispatch<JobAction>;
  dispatchSettings: Dispatch<SettingsAction>;
  dispatchUi: Dispatch<UiAction>;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({
  children,
  initial,
}: {
  children: ReactNode;
  initial?: Partial<{
    media: MediaState;
    selection: SelectionState;
    job: JobState;
    settings: SettingsState;
    ui: UiState;
  }>;
}) {
  const [media, dispatchMedia] = useReducer(
    mediaReducer,
    initial?.media ?? emptyMedia,
  );
  const [selection, dispatchSelection] = useReducer(
    selectionReducer,
    initial?.selection ?? emptySelection,
  );
  const [job, dispatchJob] = useReducer(jobReducer, initial?.job ?? emptyJob);
  const [settings, dispatchSettings] = useReducer(
    settingsReducer,
    initial?.settings ?? emptySettings,
  );
  const [ui, dispatchUi] = useReducer(uiReducer, initial?.ui ?? emptyUi);
  return (
    <Ctx.Provider
      value={{
        media,
        selection,
        job,
        settings,
        ui,
        dispatchMedia,
        dispatchSelection,
        dispatchJob,
        dispatchSettings,
        dispatchUi,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}
