import type { ReactNode } from 'react';
import { useL } from '../i18n/context';
import { fmtT, rel } from '../lib/format';
import { laneLabel } from '../lib/items';
import type { Item } from '../lib/types';
import { useBoard } from '../state/board';

const TONE = {
  grey: 'bg-grey-weak text-grey-ink', blue: 'bg-blue-weak text-blue-ink', orange: 'bg-orange-weak text-orange-ink',
  red: 'bg-red-weak text-red-ink', green: 'bg-green-weak text-green-ink', teal: 'bg-teal-weak text-teal-ink',
};
export type Tone = keyof typeof TONE;

export function Chip({ tone = 'grey', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex h-6 flex-none items-center gap-1.5 whitespace-nowrap rounded-chip px-2 text-t6 font-semibold leading-none ${TONE[tone]}`}>{children}</span>;
}

export function LaneChip({ lane }: { lane: string }) {
  const { data } = useBoard();
  return <Chip>{laneLabel(data, lane)}</Chip>;
}

export function KindChip({ it }: { it: Item }) {
  const { L } = useL();
  if (it.question) return <Chip tone="orange">{L.kind.ask}</Chip>;
  return <Chip>{(L.kind as Record<string, string>)[it.kind] || it.kind}</Chip>;
}

export const VERDICT_TONE: Record<string, Tone> = { ok: 'green', no: 'red', hold: 'orange', answer: 'blue' };

export function When({ at, className = '' }: { at?: number | null; className?: string }) {
  const { L } = useL();
  return <span className={`tabular-nums ${className}`} title={fmtT(at)}>{rel(L, at)}</span>;
}

export function Meta({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`flex min-w-0 flex-wrap items-center gap-2 text-t6 leading-snug text-ink-3 ${className}`}>{children}</div>;
}
