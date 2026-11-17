import { useState, type ReactNode } from 'react';
import { useL } from '../i18n/context';

// Long text folds to a few lines with a more / less toggle.
export function Clamp({ long, children }: { long: boolean; children: ReactNode }) {
  const { L } = useL();
  const [open, setOpen] = useState(false);
  if (!long) return <>{children}</>;
  return (
    <>
      <div className={open ? '' : 'max-h-[8.4em] overflow-hidden [mask-image:linear-gradient(#000_60%,transparent)]'}>{children}</div>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="-ml-2 mt-0.5 -mb-2 min-h-11 min-w-11 rounded-chip px-2 text-t6 font-semibold text-blue-ink">
        {open ? L.btn.less : L.btn.more}
      </button>
    </>
  );
}

export const isLong = (s?: string | null) => String(s || '').length > 220 || String(s || '').split('\n').length > 5;
