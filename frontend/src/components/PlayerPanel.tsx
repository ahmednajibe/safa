export default function PlayerPanel() {
  return (
    <section aria-label="Player" className="flex h-full min-h-[300px] rounded-panel bg-surface p-[22px]">
      <div className="flex flex-1 flex-col items-center justify-center gap-4 rounded-panel bg-well px-6 text-center">
        <div className="text-section font-semibold">Drop audio or video here</div>
        <button type="button" className="btn-secondary" disabled>
          Choose file
        </button>
        <div className="text-small text-faint">
          WAV, MP3, FLAC, M4A, MP4, MKV, MOV · stays on this computer
        </div>
      </div>
    </section>
  );
}
