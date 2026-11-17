import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import type { Day } from '../lib/laneTypes';
import { tok } from '../lib/machine';

// Tokens per day, one column each, scaled to the busiest day.
export function DaysChart({ days }: { days: Day[] }) {
  const { L } = useL();
  const max = Math.max(1, ...days.map(x => x.tokens_in + x.tokens_out));
  return (
    <div className="flex h-24 items-end gap-1.5">
      {days.map(x => (
        <div key={x.day} title={x.day + ' ' + fmt(L.use.io, { i: tok(x.tokens_in), o: tok(x.tokens_out) })}
          className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-0.5 text-t6 text-ink-3">
          <b className="whitespace-nowrap font-semibold tabular-nums text-ink-2">{tok(x.tokens_in + x.tokens_out)}</b>
          <div className="flex w-full flex-1 items-end"><i className="block min-h-0.5 w-full rounded-t-[3px] bg-blue" style={{ height: Math.round(100 * (x.tokens_in + x.tokens_out) / max) + '%' }} /></div>
          <span>{x.day.slice(8)}</span>
        </div>
      ))}
    </div>
  );
}
