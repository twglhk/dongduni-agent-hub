import { useL } from '../../i18n/context';
import { cardColor, docUrl, pipeTitle, rtTone } from '../../lib/engine';
import type { Pipeline } from '../../lib/engineTypes';
import { fmt } from '../../lib/format';
import { openDoc } from '../../state/docSheet';
import { Chip, LaneChip, Meta } from '../Chips';
import { Icon } from '../Icon';

const INK: Record<string, string> = { green: 'text-green-ink', amber: 'text-orange-ink', red: 'text-red-ink', none: 'text-ink-3' };

export function PipeCard({ eid, p }: { eid: string; p: Pipeline }) {
  const { L } = useL();
  const E = L.eg as Record<string, string>, r = p.rating, [name, sub] = pipeTitle(p);
  return (
    <div className="grid gap-2 rounded-card bg-surface px-5 py-4">
      <button type="button" disabled={!p.card} title={p.card || E.noProcCard}
        onClick={() => p.card && openDoc({ title: E.procCard + ' · ' + name, path: p.card, url: docUrl(eid, p.card) })}
        className="flex w-full min-w-0 items-start gap-2 text-left disabled:cursor-default">
        <span className="grid min-w-0 flex-1 gap-0.5">
          <b className="text-t4 font-bold leading-snug">#{p.row} {name}</b>
          {sub && <span className="text-t6 leading-normal break-words text-ink-3">{sub}</span>}
        </span>
        {r && r.n ? <Chip tone={rtTone(r.color)}>{r.pct}% · {E['s_' + r.state] || r.state}</Chip> : <Chip>{E.unrated}</Chip>}
        {p.card && <Icon name="chev" className="mt-0.5 size-[18px] text-ink-4" />}
      </button>
      <Meta>
        {p.lane && <LaneChip lane={p.lane} />}
        <span>{E.process} {p.version ? 'v' + p.version + ' · ' : ''}<span className={INK[cardColor(p.process_status)]}>{p.process_status || '—'}</span></span>
        {p.pattern_status && <span>{E.cards} <span className={INK[cardColor(p.pattern_status)]}>{p.pattern_status}</span></span>}
      </Meta>
      {p.model && <div className="text-t6 leading-normal break-words text-ink-3"><span className="mr-1.5 font-semibold text-ink-2">{E.model}</span>{p.model}</div>}
      {p.stages.length > 0 && (
        <ol className="-mx-2 grid">
          {p.stages.map(s => {
            const g = s.rating, c = s.card, color = g && g.n && g.color && g.color !== 'none' ? g.color : c ? cardColor(c.status) : 'none';
            const title = [s.by, c ? (c.version ? 'v' + c.version + ' ' : '') + c.status : E.noCard, g && g.n ? fmt(E.rated, { n: g.n }) : E.unrated].filter(Boolean).join(' · ');
            return (
              <li key={s.n} className="relative [&+&]:before:absolute [&+&]:before:top-0 [&+&]:before:right-2 [&+&]:before:left-9 [&+&]:before:h-px [&+&]:before:bg-line">
                <button type="button" disabled={!c?.path} title={title}
                  onClick={() => c?.path && openDoc({ title: fmt(E.stageCard, { n: s.n, name: s.name }), path: c.path, url: docUrl(eid, c.path) })}
                  className="pressable flex min-h-12 w-full items-center gap-3 rounded-tile px-2 py-2.5 text-left text-t5 leading-snug disabled:cursor-default disabled:active:transform-none">
                  <b className="w-4 flex-none text-center text-t6 font-semibold tabular-nums text-ink-3">{s.n}</b>
                  <span className="min-w-0 flex-1 break-words">{s.name}{s.weakest && <i aria-hidden className="ml-1.5 inline-block size-1.5 rounded-full bg-orange align-middle" />}</span>
                  <span className={`flex-none text-t6 tabular-nums ${INK[color] || INK.none}`}>{g && g.n ? g.pct + '%' : c ? c.status : E.noCard}</span>
                  {c?.path && <Icon name="chev" className="size-4 flex-none text-ink-4" />}
                </button>
              </li>
            );
          })}
        </ol>
      )}
      {r?.fixes ? <Meta>{fmt(E.fixedN, { n: r.fixes })}</Meta> : null}
    </div>
  );
}
