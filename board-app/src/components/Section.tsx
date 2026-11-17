import type { ReactNode } from 'react';
import { FoldTitle } from './prefs/FoldTitle';

export interface Fold { folded: boolean; toggle: () => void }

export function Section({ title, n, children, fold, aside }: { title: string; n?: number; children: ReactNode; fold?: Fold; aside?: ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-3 flex items-center gap-2 px-1 text-t3 font-bold leading-snug tracking-[-.01em]">
        <FoldTitle name={title} fold={fold}>
          <h2>{title}</h2>
          {n !== undefined && <span className="text-t4 font-semibold text-ink-3">{n}</span>}
        </FoldTitle>
        {aside}
      </div>
      {!fold?.folded && children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-card bg-surface px-5 py-6 text-center text-t5 text-ink-3">{children}</div>;
}
