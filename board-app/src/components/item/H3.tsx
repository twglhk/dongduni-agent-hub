import type { ReactNode } from 'react';

export function H3({ children }: { children: ReactNode }) {
  return <div className="mt-6 mb-2 flex gap-1.5 px-1 text-t5 font-semibold text-ink-3">{children}</div>;
}
