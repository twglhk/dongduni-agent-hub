import { Chip } from '../components/Chips';
import { Row, RowCard } from '../components/procs/Row';
import { SkelCards } from '../components/Skeleton';
import { Empty, Section } from '../components/Section';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { laneLabel } from '../lib/items';
import { frameWord, plain, statusTone, statusWord } from '../lib/procs';
import type { ProcLib } from '../lib/procTypes';
import { useBoard } from '../state/board';
import { openFull, openLane, openSub } from '../state/route';
import { useProcesses } from '../state/useProcesses';

export function EngineView() {
  const { L } = useL();
  const { data } = useBoard();
  const lib = useProcesses();
  const P = L.pl;
  if (!lib.data) return <SkelCards n={3} rows={0} />;
  const v = lib.data;
  const lane = (id: string) => laneLabel(data, id);
  const fullTitle = (id: string) => v.full.find(f => f.id === id)?.title || id;
  return (
    <>
      <Section title={P.full} n={v.full.length}>
        {v.full.length ? (
          <RowCard>
            {v.full.map(f => (
              <Row key={f.id} onClick={() => openFull(f.id)} label={fmt(P.open, { name: f.title })}
                title={f.title} sub={plain(f.deliverable)}
                meta={[fmt(P.subsN, { n: f.steps.length }), f.lanes.length ? f.lanes.map(lane).join(', ') : P.noLane].join(' · ')}
                right={<Chip tone={statusTone(f.status)}>{statusWord(L, f.status)}</Chip>} />
            ))}
          </RowCard>
        ) : <EmptyLib />}
      </Section>
      <Section title={P.sub} n={v.sub.length}>
        {v.sub.length ? (
          <RowCard>
            {v.sub.map(s => (
              <Row key={s.id} onClick={() => openSub(s.id)} label={fmt(P.open, { name: s.title })}
                title={s.title} sub={[framesOf(v, s.id).map(f => frameWord(L, f)).join(', '), fmt(P.stagesN, { n: s.stages.length })].filter(Boolean).join(' · ')}
                meta={[s.used_by_full.map(fullTitle).join(', '), s.used_by_lanes.map(lane).join(', ')].filter(Boolean).join(' · ') || P.noLane}
                right={<Chip tone={statusTone(s.confirmed ? 'confirmed' : s.status)}>{statusWord(L, s.confirmed ? 'confirmed' : s.status)}</Chip>} />
            ))}
          </RowCard>
        ) : <EmptyLib />}
      </Section>
      <Section title={P.byLane} n={Object.keys(v.lanes).length}>
        <RowCard>
          {laneOrder(data?.lanes.filter(l => !l.base).map(l => l.lane) || [], v).map(id => (
            <Row key={id} onClick={() => openLane(id)} label={fmt(P.open, { name: lane(id) })} title={lane(id)}
              sub={v.lanes[id].length ? v.lanes[id].map(fullTitle).join(', ') : P.laneEmpty} />
          ))}
        </RowCard>
      </Section>
      <XSections tab="engine" />
    </>
  );
}

function EmptyLib() {
  const { L } = useL();
  return <Empty><div className="text-t4 font-semibold text-ink">{L.pl.emptyLib}</div><div className="mt-1 text-t6">{L.pl.emptyLibSub}</div></Empty>;
}

export const framesOf = (v: ProcLib, sid: string) =>
  [...new Set(v.full.flatMap(f => f.steps.filter(st => st.sub === sid).map(st => st.frame)))];

const laneOrder = (board: string[], v: ProcLib) => {
  const ids = Object.keys(v.lanes);
  return [...board.filter(id => ids.includes(id)), ...ids.filter(id => !board.includes(id))];
};
