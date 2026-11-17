import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { isNum, num, type MachineInfo } from '../lib/machine';
import { Chip, type Tone } from './Chips';

const TONE: Record<string, Tone> = { g: 'green', a: 'orange', r: 'red' };

// This machine graded against its own cores and memory, the Claude usage percent when known, and why tasks wait.
export function MachineCard({ j }: { j: MachineInfo }) {
  const { L } = useL();
  const m = j.machine, U = L.mc;
  const row = (k: string, lv: string | undefined, text: string | null) => (
    <div className="flex min-w-0 items-center gap-3 text-t5">
      <span className="w-[72px] flex-none font-semibold text-ink-2">{k}</span>
      {lv && TONE[lv] ? <Chip tone={TONE[lv]}>{(U as Record<string, string>)[lv]}</Chip> : <Chip>{U.unknown}</Chip>}
      <span className="min-w-0 truncate text-ink-3 tabular-nums">{text || U.unknown}</span>
    </div>
  );
  const free = isNum(m.free_gb) ? (isNum(m.ram_gb) ? fmt(U.free, { f: num(m.free_gb, 1), t: num(m.ram_gb) }) : fmt(U.freeOnly, { f: num(m.free_gb, 1) })) : null;
  const u = j.usage;
  const claude = !u ? null : u.unknown || !Array.isArray(u.windows) ? U.unknown
    : u.windows.map(w => U[w.name === '5h' ? 'h5' : 'week'] + ' ' + (isNum(w.percent) ? Math.round(w.percent) + '%' : U.unknown)).join(' · ');
  const waits = Object.entries(j.waits || {});
  return (
    <div title={U.hint} className="grid gap-2 rounded-card bg-surface px-5 py-4">
      {row(U.load, m.load_lv, isNum(m.load_per_core) ? fmt(U.perCore, { v: num(m.load_per_core, 2), n: m.cores }) : null)}
      {row(U.cpu, m.cpu_lv, isNum(m.cpu) ? fmt(U.cpuBusy, { v: num(m.cpu) }) : null)}
      {row(U.mem, m.mem_lv, free)}
      {row(U.swap, m.swap_lv, isNum(m.swap_gb) ? fmt(U.swapUsed, { s: num(m.swap_gb, 1) }) : null)}
      {claude && <div className="flex min-w-0 items-center gap-3 text-t5"><span className="w-[72px] flex-none font-semibold text-ink-2">{U.claude}</span><span className="min-w-0 truncate text-ink-3">{claude}</span></div>}
      {waits.length > 0 && <div className="text-t6 leading-normal text-ink-3">
        <b className="mr-1 font-semibold text-ink-2">{U.waits}</b>{waits.map(([id, r]) => '#' + id + ' ' + r).join(' · ')}
      </div>}
    </div>
  );
}
