import type { ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { Icon } from '../Icon';
import type { Fold } from '../Section';

export function FoldTitle({ name, fold, children }: { name: string; fold?: Fold; children: ReactNode }) {
  const { L } = useL();
  if (!fold) return <>{children}</>;
  return (
    <button type="button" aria-expanded={!fold.folded} aria-label={fmt(fold.folded ? L.pf.foldOn : L.pf.foldOff, { name })} onClick={fold.toggle}
      className="-my-2 -ml-1 flex min-h-11 min-w-0 items-center gap-1.5 rounded-chip px-1 text-left">
      {children}<Icon name="down" className={`size-5 flex-none text-ink-3 transition-transform ${fold.folded ? '-rotate-90' : ''}`} />
    </button>
  );
}
