import { useEffect } from 'react';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { cost, isNum, rtName, tok } from '../lib/machine';
import { useBoard } from '../state/board';

const DOT: Record<string, string> = { g: 'bg-green', a: 'bg-orange', r: 'bg-red' };
const INK: Record<string, string> = { g: '', a: 'text-orange-ink', r: 'text-red-ink' };
const pctLv = (p: unknown) => !isNum(p) ? null : p < 60 ? 'g' : p < 85 ? 'a' : 'r';

export function UseBar() {
  const { L } = useL();
  const { data, mach } = useBoard();
  const rts = (data?.usage?.runtimes || []).filter(r => (r.tokens_in || 0) + (r.tokens_out || 0) > 0);
  const m = mach?.machine, u = mach?.usage;
  const wins = u && Array.isArray(u.windows) && !u.unknown ? u.windows : null;
  const show = rts.length > 0 || !!m;
  useEffect(() => { document.body.classList.toggle('has-use', show); return () => document.body.classList.remove('has-use'); }, [show]);
  if (!show) return null;
  return (
    <div role="status" aria-label={L.sec.usage} title={L.use.hint}
      className="no-scrollbar mx-auto flex h-7 max-w-col items-center gap-3 overflow-x-auto whitespace-nowrap border-b border-line px-5 text-t6 text-ink-3 tabular-nums">
      {rts.length > 0 && <span className="font-bold text-ink-2">{L.use.day}</span>}
      {rts.map(r => (
        <span key={r.runtime}><b className="mr-1 font-semibold text-ink-2">{rtName(r.runtime)}</b>
          {fmt(L.use.io, { i: tok(r.tokens_in), o: tok(r.tokens_out) }) + (isNum(r.cost) ? ' · ' + cost(r.cost) : '')}</span>
      ))}
      {m && (
        <span className={INK[m.lv || '']}>
          <i className={`mr-1 inline-block size-2 rounded-full align-middle ${DOT[m.lv || ''] || 'bg-ink-4'}`} />
          <b className="mr-1 font-semibold text-ink-2">{L.mc.title}</b>{(L.mc as Record<string, string>)[m.lv || ''] || L.mc.unknown}
        </span>
      )}
      {u && (
        <span><b className="mr-1 font-semibold text-ink-2">{L.mc.claude}</b>
          {wins ? wins.map((w, i) => (
            <span key={w.name} className={INK[pctLv(w.percent) || '']}>{i ? ' · ' : ''}{L.mc[w.name === '5h' ? 'h5' : 'week']} <b className="font-semibold">{isNum(w.percent) ? Math.round(w.percent) + '%' : L.mc.unknown}</b></span>
          )) : L.mc.unknown}
        </span>
      )}
    </div>
  );
}
