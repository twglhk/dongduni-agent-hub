import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { cost, isNum, rtName, tok } from '../../lib/machine';
import type { Lane } from '../../lib/types';
import { openLane } from '../../state/route';
import { Chip } from '../Chips';
import { StatusChip } from '../lane/StatusChip';

export function TokenLine({ k }: { k?: { new?: number; cache?: number; out?: number; turns?: number } | null }) {
  const { L } = useL();
  if (!k || !((k.new || 0) + (k.cache || 0) + (k.out || 0))) return null;
  return <div className="text-t6 text-ink-3">{fmt(L.use.task, { n: tok(k.new), c: tok(k.cache), o: tok(k.out), t: k.turns || 0 })}</div>;
}

const CLAMP2 = 'line-clamp-2 leading-normal';

// One lane: what it is doing, its process and stage, the model it runs and what it used today. It opens the lane sheet.
export function LaneCard({ l }: { l: Lane }) {
  const { L } = useL();
  const model = [l.model, l.effort].filter(Boolean).join(' · ');
  const proc = l.process ? [l.process, l.stage ? fmt(L.lv.stageN, { n: l.stage }) : ''].filter(Boolean).join(' · ') : '';
  const u = l.usage, used = u && ((u.tokens_in || 0) + (u.tokens_out || 0)) > 0;
  return (
    <button type="button" onClick={() => openLane(l.lane)}
      className="pressable grid w-full content-start gap-1.5 rounded-card bg-surface px-5 py-4 text-left">
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate text-t4 font-bold">{l.label || l.lane}</span>
        {l.runtime && <Chip>{rtName(l.runtime)}</Chip>}
        <span className="ml-auto flex-none"><StatusChip status={l.status} /></span>
      </div>
      {l.task_id ? <div className={`text-t5 font-semibold ${CLAMP2}`}><span className="mr-1 font-medium text-ink-3">#{l.task_id}</span>{l.task_title}</div>
        : l.doing ? null : <div className="text-t5 text-ink-3">{L.label.noTask}</div>}
      {l.doing && <div className={`${l.task_id ? 'text-t6 text-ink-2' : 'text-t5 font-semibold'} ${CLAMP2}`}>{l.doing}</div>}
      {(proc || model || l.fixing) && (
        <div className="flex flex-wrap items-center gap-2 text-t6 text-ink-3">
          {proc && <span>{proc}</span>}
          {model && <Chip>{model}</Chip>}
          {l.fixing && <Chip tone="orange">{L.lv.fixing}</Chip>}
        </div>
      )}
      {!!l.ctx_k && l.ctx_k > 0 && <div className="text-t6 text-ink-3">{L.label.ctx} {l.ctx_k}K</div>}
      <TokenLine k={l.task_tokens} />
      {used && <div className="text-t6 text-ink-3">{fmt(L.use.today, { i: tok(u.tokens_in), o: tok(u.tokens_out) }) + (isNum(u.cost) ? ' · ' + cost(u.cost) : '')}</div>}
    </button>
  );
}
