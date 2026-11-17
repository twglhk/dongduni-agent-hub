import { useL } from '../i18n/context';
import { fmt, rel } from '../lib/format';
import { laneLabel } from '../lib/items';
import type { Task } from '../lib/types';
import { useBoard } from '../state/board';
import { Chip, type Tone } from './Chips';

const TONE: Record<string, Tone> = { queued: 'orange', running: 'blue', wrapping: 'teal', closed: 'green', cancelled: 'red' };

export function TaskRow({ t }: { t: Task }) {
  const { L } = useL();
  const { data, mach } = useBoard();
  const why = t.state === 'queued' ? mach?.waits?.[t.id] : null;
  return (
    <div className="relative flex min-h-16 items-center gap-3 px-5 py-3 [&+&]:before:absolute [&+&]:before:inset-x-5 [&+&]:before:top-0 [&+&]:before:border-t [&+&]:before:border-line">
      <Chip tone={TONE[t.state] || 'grey'}>{(L.task as Record<string, string>)[t.state] || t.state}</Chip>
      <div className="min-w-0 flex-1">
        <div className="text-t5 font-semibold leading-snug [overflow-wrap:anywhere]">#{t.id} {t.hidden ? L.label.otherWork : t.title}</div>
        <div className="mt-0.5 text-t6 text-ink-3">{laneLabel(data, t.lane)} · {rel(L, t.closed || t.started || t.created)}</div>
        {why && <div className="mt-0.5 text-t6 text-orange-ink">{fmt(L.mc.wait, { r: why })}</div>}
      </div>
    </div>
  );
}
