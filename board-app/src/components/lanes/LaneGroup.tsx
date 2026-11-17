import { baseOf } from '../../lib/laneRows';
import type { Lane } from '../../lib/types';
import { LaneCard } from './LaneCard';

export function LaneGroup({ rows }: { rows: Lane[] }) {
  const [head, ...rest] = rows;
  return (
    <div className="grid content-start gap-2">
      <LaneCard key={head.lane} l={head} />
      {rest.length > 0 && (
        <div className="grid gap-2 border-l-[3px] border-line-2 pl-3" aria-label={baseOf(head)}>
          {rest.map(l => <LaneCard key={l.lane} l={l} />)}
        </div>
      )}
    </div>
  );
}
