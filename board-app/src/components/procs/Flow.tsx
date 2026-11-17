import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { frameWord, inkOf, statusWord } from '../../lib/procs';
import type { FullProcess, ProcLib } from '../../lib/procTypes';
import { openSub } from '../../state/route';
import { Icon } from '../Icon';

const DOT = 'absolute top-[18px] -left-[22px] size-2.5 rounded-full';

export function Flow({ f, v }: { f: FullProcess; v: ProcLib }) {
  const { L } = useL();
  const P = L.pl;
  return (
    <div className="mt-2">
      <div className="mb-3 text-t6 text-ink-3">{P.path}</div>
      <div className="rounded-tile bg-surface-3 px-4 py-3">
        <div className="text-t6 font-semibold text-ink-3">{P.kindFull}</div>
        <div className="text-t5 font-bold leading-snug">{f.title}</div>
      </div>
      <ol className="relative ml-[13px] border-l-2 border-line-2 pt-3 pl-4">
        {f.steps.map(st => {
          const s = v.sub.find(x => x.id === st.sub);
          const rate = st.rating;
          return (
            <li key={st.n} className="relative pb-3 last:pb-0">
              <i aria-hidden className={`${DOT} ${s ? 'bg-ink-3' : 'bg-orange'}`} />
              <div className="overflow-hidden rounded-tile border border-line">
                <button type="button" disabled={!s} onClick={() => s && openSub(s.id)} aria-label={s ? fmt(P.open, { name: s.title }) : undefined}
                  className="pressable flex min-h-[52px] w-full items-center gap-3 px-3 py-2.5 text-left disabled:cursor-default">
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <span className="text-t6 font-semibold text-ink-3">{P.kindSub} {st.n} · {frameWord(L, st.frame)}</span>
                    <span className="text-t4 font-bold leading-snug break-words">{s ? s.title : fmt(P.notInLib, { id: st.sub })}</span>
                  </span>
                  <span className={`flex-none text-t6 tabular-nums ${rate ? inkOf(rate.color) : 'text-ink-3'}`}>
                    {rate ? rate.pct + '%' : statusWord(L, s ? (s.confirmed ? 'confirmed' : s.status) : null)}
                  </span>
                  {s && <Icon name="chev" className="size-5 text-ink-4" />}
                </button>
                {s && s.stages.length > 0 && (
                  <ol className="border-t border-line bg-surface-2 px-3 py-1.5">
                    {s.stages.map(x => {
                      const g = st.stages?.[x.n];
                      return (
                        <li key={x.n} className="flex min-h-9 items-center gap-2.5 py-1 text-t5 leading-snug">
                          <span className="w-4 flex-none text-center text-t6 font-semibold tabular-nums text-ink-3">{x.n}</span>
                          <span className="min-w-0 flex-1 break-words">{x.name}{g?.weakest && <i aria-hidden className="ml-1.5 inline-block size-1.5 rounded-full bg-orange align-middle" />}</span>
                          {g?.n ? <span className={`flex-none text-t6 tabular-nums ${inkOf(g.color)}`}>{g.pct}%</span> : null}
                        </li>
                      );
                    })}
                    {s.skills.length > 0 && (
                      <li className="flex flex-wrap items-center gap-1.5 border-t border-line py-2 text-t6 text-ink-3">
                        <span className="font-semibold">{P.skillOf}</span>
                        {s.skills.map(k => <span key={k.name} className={`rounded-chip bg-surface px-2 py-0.5 ${k.found ? 'text-ink-2' : 'text-orange-ink'}`}>{k.name}</span>)}
                      </li>
                    )}
                  </ol>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
