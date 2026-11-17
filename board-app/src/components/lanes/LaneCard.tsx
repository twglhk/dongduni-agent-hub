import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { cost, isNum, rtName, tok } from '../../lib/machine';
import type { Lane } from '../../lib/types';
import { openLane, openSession } from '../../state/route';
import { Chip } from '../Chips';
import { StatusChip } from '../lane/StatusChip';

export function TokenLine({ k }: { k?: { new?: number; cache?: number; out?: number; turns?: number } | null }) {
  const { L } = useL();
  if (!k || !((k.new || 0) + (k.cache || 0) + (k.out || 0))) return null;
  return <div className="text-t6 text-ink-3">{fmt(L.use.task, { n: tok(k.new), c: tok(k.cache), o: tok(k.out), t: k.turns || 0 })}</div>;
}

const CLAMP2 = 'line-clamp-2 leading-normal';

export function LaneCard({ l }: { l: Lane }) {
  const { L } = useL();
  const baseLabel = l.base ? (l.label || l.lane).replace(/ #\d+$/, '') : l.label || l.lane;
  const model = [l.model, l.effort].filter(Boolean).join(' · ');
  const proc = l.process ? [l.process, l.stage ? fmt(L.lv.stageN, { n: l.stage }) : ''].filter(Boolean).join(' · ') : '';
  const u = l.usage, used = u && ((u.tokens_in || 0) + (u.tokens_out || 0)) > 0;
  return (
    <div role="button" tabIndex={0} onClick={() => openSession(l.lane)}
      onKeyDown={e => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openSession(l.lane); } }}
      className="pressable grid w-full cursor-pointer content-start gap-1.5 rounded-card bg-surface px-5 py-4 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue">
      <div className="flex min-w-0 items-center gap-2">
        <button type="button" aria-label={fmt(L.ln.laneOpen, { lane: baseLabel })} onClick={e => { e.stopPropagation(); openLane(l.base || l.lane); }}
          className="-my-2 min-w-0 truncate rounded-chip py-2 text-left text-t4 font-bold underline decoration-line-2 decoration-1 underline-offset-4">{baseLabel}</button>
        {l.base && <Chip>#{l.lane.slice(l.lane.indexOf('#') + 1)}</Chip>}
        {l.runtime && <Chip>{rtName(l.runtime)}</Chip>}
        <span className="ml-auto flex-none"><StatusChip status={l.status} /></span>
      </div>
      {l.task_id ? <div className={`text-t5 font-semibold ${CLAMP2}`}><span className="mr-1 font-medium text-ink-3">#{l.task_id}</span>{l.task_title}{l.requester && <span className="ml-2 whitespace-nowrap text-t6 font-semibold text-ink-3">· {fmt(L.lv.by, { who: l.requester })}</span>}</div>
        : l.doing || l.memo ? null : <div className="text-t5 text-ink-3">{L.label.noTask}</div>}
      {l.memo && <div className={`text-t5 ${CLAMP2}`}><span className="mr-1 font-medium text-ink-3">{fmt(L.ln.memo, { n: l.memo.id })}</span>{l.memo.title}</div>}
      {l.doing && !l.memo && <div className={`${l.task_id ? 'text-t6 text-ink-2' : 'text-t5 font-semibold'} ${CLAMP2}`}>{l.doing}</div>}
      {l.held && <div className="rounded-chip bg-orange-weak px-2 py-1 text-t6 leading-snug text-orange-ink">{l.held}</div>}
      {l.wait && <div className="rounded-chip bg-orange-weak px-2 py-1 text-t6 leading-snug text-orange-ink">{l.wait}</div>}
      {(proc || model || l.fixing) && (
        <div className="flex flex-wrap items-center gap-2 text-t6 text-ink-3">
          {proc && <span>{proc}</span>}
          {model && <Chip>{model}</Chip>}
          {l.fixing && <Chip tone="orange">{L.lv.fixing}</Chip>}
        </div>
      )}
      {l.account && <div className="text-t6 text-ink-3">{L.ca.acct} · {l.account}</div>}
      {!!l.ctx_k && l.ctx_k > 0 && <div className="text-t6 text-ink-3">{L.label.ctx} {l.ctx_k}K</div>}
      {l.branch && <div className="flex min-w-0 items-center gap-1.5 text-t6 text-ink-3">{L.lv.branch}<code className="truncate rounded-[6px] bg-surface-3 px-1.5 py-px font-mono text-ink-2">{l.branch}</code></div>}
      <TokenLine k={l.task_tokens} />
      {used && <div className="text-t6 text-ink-3">{fmt(L.use.today, { i: tok(u.tokens_in), o: tok(u.tokens_out) }) + (isNum(u.cost) ? ' · ' + cost(u.cost) : '')}</div>}
    </div>
  );
}
