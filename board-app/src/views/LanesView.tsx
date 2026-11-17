import { Fragment } from 'react';
import { Chip } from '../components/Chips';
import { LaneAdd } from '../components/lanes/LaneAdd';
import { LaneCard } from '../components/lanes/LaneCard';
import { Stepper } from '../components/lanes/Stepper';
import { FoldTitle } from '../components/prefs/FoldTitle';
import { ReportCard } from '../components/history/ReportCard';
import { Empty, Section } from '../components/Section';
import { TaskRow } from '../components/TaskRow';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { isHidden, ordered } from '../lib/prefs';
import type { BoardState } from '../lib/types';
import { useFold, usePrefs } from '../state/prefs';
import { useCap } from '../state/useSettings';

export function LanesView({ d, onImage, onAdd }: { d: BoardState; onImage: (imgs: string[], i: number) => void; onAdd: () => void }) {
  const { L } = useL();
  const { busy, setCap } = useCap();
  const cap = d.lane_cap;
  const max = cap && cap.max > 0 ? cap.max : 10;
  const live = d.tasks.filter(t => ['queued', 'running', 'wrapping'].includes(t.state)).sort((a, b) => a.id - b.id);
  const { p } = usePrefs();
  const fold = useFold();
  const laneFold = fold('lanes', 'lanes');
  const parts = {
    lanes: (
      <section className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 px-1">
          <FoldTitle name={L.sec.lanes} fold={laneFold}><h2 className="text-t3 font-bold leading-snug tracking-[-.01em]">{L.sec.lanes}</h2></FoldTitle>
          <span className="ml-auto flex flex-wrap items-center gap-2">
            {d.paused && <Chip tone="orange">{L.label.paused}</Chip>}
            {cap && <span className={`text-t5 font-semibold tabular-nums ${cap.used >= cap.cap ? 'text-orange-ink' : 'text-ink-3'}`}>{fmt(L.label.cap, { u: cap.used, c: cap.cap })}</span>}
            {cap && <Stepper n={Math.max(1, Math.min(max, cap.cap || 1))} max={max} busy={busy} label={L.cap.at} showLabel onSet={setCap} />}
          </span>
        </div>
        {!laneFold?.folded && (
          <>
            {d.lanes.length ? <div className="grid grid-cols-1 gap-3 min-[600px]:grid-cols-2">{d.lanes.map(l => <LaneCard key={l.lane} l={l} />)}</div> : <Empty>{L.empty.lanes}</Empty>}
            {cap && <div className="px-1 pt-2 text-t6 leading-normal text-ink-3">{L.cap.hint}</div>}
            <LaneAdd onOpen={onAdd} />
          </>
        )}
      </section>
    ),
    tasks: (
      <Section title={L.sec.tasks} n={live.length} fold={fold('lanes', 'tasks')}>
        {live.length ? <div className="overflow-hidden rounded-card bg-surface">{live.map(t => <TaskRow key={t.id} t={t} />)}</div> : <Empty>{L.empty.tasks}</Empty>}
      </Section>
    ),
    reports: (
      <Section title={L.sec.reports} fold={fold('lanes', 'reports')}>
        {d.reports.length ? <div className="grid grid-cols-1 gap-2">{d.reports.map(r => <ReportCard key={r.id} r={r} onImage={onImage} />)}</div> : <Empty>{L.empty.reports}</Empty>}
      </Section>
    ),
  };
  return (
    <>
      {ordered(p, 'lanes').filter(id => !isHidden(p, 'lanes', id)).map(id => <Fragment key={id}>{parts[id]}</Fragment>)}
      <XSections tab="lanes" />
    </>
  );
}
