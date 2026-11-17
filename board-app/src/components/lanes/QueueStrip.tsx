import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import type { LaneQueue, QueueItem } from '../../lib/types';

const SHOWN = 8;

function Pill({ x, now }: { x: QueueItem; now?: boolean }) {
  const { L } = useL();
  const tip = x.hidden ? `#${x.id}` : `#${x.id} ${x.title}` + (x.session && x.session > 1 ? ` · ${fmt(L.ln.sessionN, { n: x.session })}` : '') + (x.why ? ` · ${x.why}` : '');
  return (
    <span title={tip} aria-label={tip}
      className={`inline-flex h-5 items-center rounded-[6px] border px-1.5 font-mono text-t6 tabular-nums ${now ? 'border-blue bg-blue-weak text-blue-ink' : 'border-line-2 text-ink-2'}`}>
      {x.hidden ? '·' : '#' + x.id}
    </span>
  );
}

export function QueueStrip({ q, showWhy }: { q?: LaneQueue; showWhy: boolean }) {
  const { L } = useL();
  if (!q || (!q.now.length && !q.waiting)) return null;
  const blocked = showWhy ? q.wait.find(x => x.why && !x.hidden) : undefined;
  return (
    <div className="grid gap-1 px-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-t6 text-ink-3">
        {q.now.length > 0 && (
          <span className="flex flex-wrap items-center gap-1"><i className="not-italic">{L.ln.now}</i>{q.now.map(x => <Pill key={x.id} x={x} now />)}</span>
        )}
        {q.waiting > 0 && (
          <span className="flex flex-wrap items-center gap-1">
            <i className="not-italic">{fmt(L.ln.waitingN, { n: q.waiting })}</i>
            {q.wait.slice(0, SHOWN).map(x => <Pill key={x.id} x={x} />)}
            {q.waiting > SHOWN && <span className="text-ink-3">{fmt(L.ln.more, { n: q.waiting - SHOWN })}</span>}
          </span>
        )}
      </div>
      {blocked && <div className="text-t6 leading-snug text-orange-ink">#{blocked.id} {blocked.why}</div>}
    </div>
  );
}
