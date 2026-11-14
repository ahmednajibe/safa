export default function Dock() {
  return (
    <footer className="flex h-[72px] shrink-0 items-center gap-3 px-4 lg:px-3.5">
      <button type="button" className="btn-secondary" disabled aria-label="Output format">
        WAV
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <div className="min-w-0 flex-1" />
      <button type="button" className="btn-secondary" disabled>
        Preview region
      </button>
      <span title="Add a file to start">
        <button type="button" className="btn-primary" disabled>
          Process
        </button>
      </span>
    </footer>
  );
}
