import { useL } from '../../i18n/context';
import type { Memo } from '../../lib/types';
import { AttachStrip } from '../AttachStrip';
import { Chip, Meta, When, type Tone } from '../Chips';
import { Prose } from '../Prose';

const TONE: Record<string, Tone> = { replied: 'green', delivered: 'grey', wait: 'orange' };

export function MemoRow({ m, label }: { m: Memo; label?: string }) {
  const { L } = useL();
  const reps = m.replies || [];
  const st = reps.length ? 'replied' : m.delivered ? 'delivered' : 'wait';
  return (
    <div className="rounded-box bg-surface px-5 py-4 text-t5 leading-[1.55]">
      <Meta>{label && <Chip tone="blue">{label}</Chip>}<Chip tone={TONE[st]}>{(L.memo as Record<string, string>)[st]}</Chip>
        <span>#{m.id}</span>{m.sender && <span>{m.sender}</span>}<When at={m.created} className="ml-auto" /></Meta>
      <div className="mt-1.5 whitespace-pre-wrap [overflow-wrap:anywhere]">{m.text}</div>
      <AttachStrip text={m.text} />
      {reps.map((r, i) => (
        <div key={i} className="mt-3 rounded-tile bg-blue-weak px-4 py-3">
          <Meta><span>{L.label.reply}</span><When at={r.created} /></Meta>
          <Prose text={r.text} className="chat mt-1" />
        </div>
      ))}
    </div>
  );
}
