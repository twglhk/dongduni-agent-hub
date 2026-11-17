import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import type { Disk } from '../lib/machine';
import { Chip } from './Chips';

const BAR: Record<string, string> = { g: 'bg-green', a: 'bg-orange', r: 'bg-red' };
const TONE = { g: 'green', a: 'orange', r: 'red' } as const;
const gb = (v?: number | null) => (v == null ? '?' : v >= 100 ? String(Math.round(v)) : v.toFixed(1));

// Each disk's size, use and free space, read fresh with every machine poll.
export function DiskCard({ disks }: { disks: Disk[] }) {
  const { L } = useL();
  const D = L.disk;
  return (
    <div title={D.hint} className="grid gap-4 rounded-card bg-surface px-5 py-4">
      {disks.map(d => {
        const lv = d.lv && d.lv in TONE ? d.lv as keyof typeof TONE : null;
        return (
          <div key={d.mount} className="grid gap-1.5">
            <div className="flex min-w-0 items-center gap-2 text-t5">
              <span className="min-w-0 flex-1 truncate font-semibold text-ink-2">{d.mount === '/' ? D.root : d.mount}</span>
              {lv ? <Chip tone={TONE[lv]}>{d.used_pct}%</Chip> : <Chip>{L.mc.unknown}</Chip>}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={d.used_pct ?? undefined}>
              <div className={`h-full rounded-full ${BAR[d.lv || ''] || 'bg-ink-4'}`} style={{ width: `${Math.min(100, d.used_pct || 0)}%` }} />
            </div>
            <div className="text-t6 text-ink-3 tabular-nums">{fmt(D.used, { u: gb(d.used_gb), t: gb(d.total_gb), f: gb(d.free_gb) })}</div>
          </div>
        );
      })}
    </div>
  );
}
