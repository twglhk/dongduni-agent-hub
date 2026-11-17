import { useEffect, useRef, useState } from 'react';
import { useL } from '../../i18n/context';
import { openPrefs, usePrefs } from '../../state/prefs';
import { Icon } from '../Icon';

export function PrefsTip() {
  const { L } = useL();
  const { p, ok, set } = usePrefs();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  const fresh = ok === true && !p.seen.includes('prefs');

  useEffect(() => {
    if (!open) return;
    const out = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', out);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', out); document.removeEventListener('keydown', esc); };
  }, [open]);

  if (ok === false) return null;
  const toggle = () => {
    setOpen(o => !o);
    if (fresh) set({ seen: [...p.seen, 'prefs'] });
  };
  return (
    <span ref={box} className="relative -my-2 -mr-2 ml-auto flex-none">
      <button type="button" aria-label={L.pf.tipLabel + (fresh ? ` · ${L.pf.tipNew}` : '')} aria-expanded={open} aria-controls="prefs-tip" onClick={toggle}
        className="relative flex size-11 items-center justify-center rounded-full text-ink-3 active:bg-[var(--press)]">
        <Icon name="info" className="size-[22px]" stroke={2} />
        {fresh && <i className="absolute top-2 right-2 size-2 rounded-full bg-red ring-2 ring-[var(--bg)]" />}
      </button>
      {open && (
        <div id="prefs-tip" role="dialog" aria-label={L.pf.tipLabel}
          className="absolute top-full right-0 z-[25] mt-1 w-[min(320px,calc(100vw-40px))] rounded-box bg-surface p-4 text-left font-normal tracking-normal shadow-[0_8px_28px_rgba(0,0,0,.18)]">
          <p className="text-t5 leading-normal text-ink">{L.pf.tip}</p>
          <button type="button" onClick={() => { setOpen(false); openPrefs(); }}
            className="mt-3 flex h-11 w-full items-center justify-center rounded-tile bg-blue-weak text-t5 font-semibold text-blue-ink">{L.pf.open}</button>
        </div>
      )}
    </span>
  );
}
