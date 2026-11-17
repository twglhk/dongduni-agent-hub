import type { ReactNode } from 'react';

export function Section({ title, n, children }: { title: string; n?: number; children: ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-3 flex items-center gap-2 px-1 text-t3 font-bold leading-snug tracking-[-.01em]">
        <h2>{title}</h2>
        {n !== undefined && <span className="text-t4 font-semibold text-ink-3">{n}</span>}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-card bg-surface px-5 py-6 text-center text-t5 text-ink-3">{children}</div>;
}
