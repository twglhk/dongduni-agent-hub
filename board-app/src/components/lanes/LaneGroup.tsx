import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { baseOf } from '../../lib/laneRows';
import type { Lane } from '../../lib/types';
import { useBoard } from '../../state/board';
import { useSessions } from '../../state/useSettings';
import { LaneCard } from './LaneCard';
import { QueueStrip } from './QueueStrip';
import { Stepper } from './Stepper';

export function LaneGroup({ rows }: { rows: Lane[] }) {
  const { L } = useL();
  const { data } = useBoard();
  const { busy, setSessions } = useSessions();
  const [head, ...rest] = rows;
  const max = data?.lane_cap?.sessions_max || 5;
  return (
    <div className="grid content-start gap-2">
      {head.sessions != null && (
        <div className="flex items-center justify-end px-1">
          <Stepper n={head.sessions} max={max} busy={busy} label={L.ln.sessions} showLabel
            prefix={fmt(L.ln.sessionsOf, { lane: head.label || head.lane })} onSet={n => setSessions(head.lane, n)} />
        </div>
      )}
      <QueueStrip q={data?.queues?.[head.lane]} showWhy={!head.wait} />
      <LaneCard key={head.lane} l={head} />
      {rest.length > 0 && (
        <div className="grid gap-2 border-l-[3px] border-line-2 pl-3" aria-label={baseOf(head)}>
          {rest.map(l => <LaneCard key={l.lane} l={l} />)}
        </div>
      )}
    </div>
  );
}
