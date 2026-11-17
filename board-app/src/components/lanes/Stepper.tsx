import { useEffect, useRef } from 'react';
import { useL } from '../../i18n/context';
import { Icon } from '../Icon';

interface Props { n: number; max: number; busy: boolean; label: string; showLabel?: boolean; prefix?: string; onSet: (n: number) => void }

export function Stepper({ n, max, busy, label, showLabel, prefix, onSet }: Props) {
  const { L } = useL();
  const less = useRef<HTMLButtonElement>(null), more = useRef<HTMLButtonElement>(null), dir = useRef(0);
  useEffect(() => {
    if (busy || !dir.current) return;
    const want = dir.current > 0 ? (n >= max ? less : more) : (n <= 1 ? more : less);
    dir.current = 0;
    if (!document.activeElement || document.activeElement === document.body) want.current?.focus();
  }, [busy, n, max]);
  const btn = (d: number, ref: typeof less, off: boolean, text: string) => (
    <button ref={ref} type="button" aria-label={(prefix ? prefix + ' ' : '') + text} disabled={busy || off}
      onClick={() => { dir.current = d; onSet(n + d); }}
      className="-my-1.5 flex size-11 min-h-11 items-center justify-center rounded-full text-ink-2 disabled:cursor-default disabled:text-ink-4 [&:active:not(:disabled)_svg]:scale-80">
      <Icon name={d > 0 ? 'plus' : 'minus'} className="size-[18px] transition-transform" stroke={2.4} />
    </button>
  );
  return (
    <span role="group" aria-label={label} aria-busy={busy} className="inline-flex items-center gap-2">
      {showLabel && <span aria-hidden="true" className="whitespace-nowrap text-t6 font-semibold text-ink-3">{label}</span>}
      <span className="inline-flex h-8 items-center rounded-2xl bg-surface">
        {btn(-1, less, n <= 1, L.cap.less)}
        <b className="min-w-5 text-center text-t5 font-bold tabular-nums">{n}</b>
        {btn(1, more, n >= max, L.cap.more)}
      </span>
    </span>
  );
}
