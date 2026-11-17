import { Chip } from '../components/Chips';
import { LaneAdd } from '../components/lanes/LaneAdd';
import { LaneCard } from '../components/lanes/LaneCard';
import { Stepper } from '../components/lanes/Stepper';
import { ReportCard } from '../components/history/ReportCard';
import { Empty, Section } from '../components/Section';
import { TaskRow } from '../components/TaskRow';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import type { BoardState } from '../lib/types';
import { useCap } from '../state/useSettings';

export function LanesView({ d, onImage }: { d: BoardState; onImage: (imgs: string[], i: number) => void }) {
  const { L } = useL();
  const { busy, setCap } = useCap();
  const cap = d.lane_cap;
  const max = cap && cap.max > 0 ? cap.max : 10;
  const live = d.tasks.filter(t => ['queued', 'running', 'wrapping'].includes(t.state)).sort((a, b) => a.id - b.id);
  return (
    <>
      <section className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 px-1">
          <h2 className="text-t3 font-bold leading-snug tracking-[-.01em]">{L.sec.lanes}</h2>
          <span className="ml-auto flex flex-wrap items-center gap-2">
            {d.paused && <Chip tone="orange">{L.label.paused}</Chip>}
            {cap && <span className={`text-t5 font-semibold tabular-nums ${cap.used >= cap.cap ? 'text-orange-ink' : 'text-ink-3'}`}>{fmt(L.label.cap, { u: cap.used, c: cap.cap })}</span>}
            {cap && <Stepper n={Math.max(1, Math.min(max, cap.cap || 1))} max={max} busy={busy} label={L.cap.at} showLabel onSet={setCap} />}
          </span>
        </div>
        {d.lanes.length ? <div className="grid grid-cols-1 gap-3 min-[600px]:grid-cols-2">{d.lanes.map(l => <LaneCard key={l.lane} l={l} />)}</div> : <Empty>{L.empty.lanes}</Empty>}
        {cap && <div className="px-1 pt-2 text-t6 leading-normal text-ink-3">{L.cap.hint}</div>}
        <LaneAdd d={d} />
      </section>
      <Section title={L.sec.tasks} n={live.length}>
        {live.length ? <div className="overflow-hidden rounded-card bg-surface">{live.map(t => <TaskRow key={t.id} t={t} />)}</div> : <Empty>{L.empty.tasks}</Empty>}
      </Section>
      <Section title={L.sec.reports}>
        {d.reports.length ? <div className="grid grid-cols-1 gap-2">{d.reports.map(r => <ReportCard key={r.id} r={r} onImage={onImage} />)}</div> : <Empty>{L.empty.reports}</Empty>}
      </Section>
      <XSections tab="lanes" />
    </>
  );
}
