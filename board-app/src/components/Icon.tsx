export const ICON = {
  hub: 'M3 12l2-7h14l2 7v7H3zM3 12h5l2 3h4l2-3h5',
  engine: 'M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5',
  lanes: 'M4 5h16v5H4zM4 14h16v5H4z',
  history: 'M12 7v5l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  info: 'M12 3a9 9 0 110 18 9 9 0 010-18zM12 11v5.5M12 7.5v.01',
  chev: 'M9 5l7 7-7 7', prev: 'M15 5l-7 7 7 7', next: 'M9 5l7 7-7 7',
  close: 'M6 6l12 12M18 6L6 18', check: 'M5 12l5 5L20 7',
  clip: 'M21.4 11l-9.2 9.2a6 6 0 01-8.5-8.5l9.2-9.2a4 4 0 015.7 5.7l-9.2 9.2a2 2 0 01-2.8-2.8l8.5-8.5',
  file: 'M6 2h8l5 5v15H6zM14 2v5h5', dl: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 017 0v3',
  down: 'M6 9l6 6 6-6',
  plus: 'M12 5v14M5 12h14', minus: 'M5 12h14',
  tune: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
  ext: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  warn: 'M12 9v4.5M12 17v.01M10.3 4.2L2.6 17.5A2 2 0 004.3 20.5h15.4a2 2 0 001.7-3L13.7 4.2a2 2 0 00-3.4 0z',
} as const;

export type IconName = keyof typeof ICON;

export function Icon({ name, d, className = 'size-5', stroke = 2.2 }: { name?: IconName; d?: string; className?: string; stroke?: number }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={`flex-none fill-none stroke-current ${className}`}
      strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round">
      <path d={d || ICON[name || 'ext']} />
    </svg>
  );
}
