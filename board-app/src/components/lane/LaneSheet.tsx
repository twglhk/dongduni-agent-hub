import { type ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import type { LaneDetail } from '../../lib/laneTypes';
import { rtName, tok } from '../../lib/machine';
import type { Tokens } from '../../lib/types';
import type { ProcLib } from '../../lib/procTypes';
import { closeItem, openFull } from '../../state/route';
import { Chip } from '../Chips';
import { Row } from '../procs/Row';
import { DaysChart } from '../DaysChart';
import { Empty } from '../Section';
import { SkelLines } from '../Skeleton';
import { SheetHead, type SheetContent } from '../Sheet';
import { StatusChip } from './StatusChip';

export function Label({ children }: { children: ReactNode }) {
  return <h3 className="mt-7 mb-2 text-t6 font-semibold text-ink-3 first:mt-3">{children}</h3>;
}

const ROW = 'relative flex min-h-[52px] w-full items-center gap-3 py-3 [&+&]:before:absolute [&+&]:before:inset-x-0 [&+&]:before:top-0 [&+&]:before:border-t [&+&]:before:border-line';

function Kv({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className={ROW}>
      <span className="flex-none text-t4 text-ink-2">{k}</span>
      <span className="min-w-0 flex-1 text-right text-t4 font-semibold">{v}</span>
    </div>
  );
}

function useTokens() {
  const { L } = useL();
  return (k?: Tokens | null) => k && (k.new || 0) + (k.cache || 0) + (k.out || 0)
    ? fmt(L.use.task, { n: tok(k.new), c: tok(k.cache), o: tok(k.out), t: k.turns || 0 }) : null;
}

function Uses({ id, lib }: { id: string; lib: ProcLib | null }) {
  const { L } = useL();
  const ids = lib?.lanes[id];
  if (!ids) return null;
  return (
    <>
      <Label>{L.pl.laneUses}</Label>
      {ids.length ? ids.map(fid => {
        const f = lib!.full.find(x => x.id === fid);
        return <Row key={fid} title={f?.title || fid} sub={f ? fmt(L.pl.subsN, { n: f.steps.length }) : undefined}
          onClick={f ? () => openFull(fid) : undefined} label={fmt(L.pl.open, { name: f?.title || fid })} />;
      }) : <Row title={L.pl.laneEmpty} />}
    </>
  );
}

function Body({ id, d, lib }: { id: string; d: LaneDetail; lib: ProcLib | null }) {
  const { L } = useL();
  const tokens = useTokens();
  const t = d.task, p = d.process, f = d.fixing;
  const model = t ? [t.model, t.effort].filter(Boolean).join(' · ') : '';
  const tk = t && tokens(t.tokens);
  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Chip>{rtName(d.runtime)}</Chip><StatusChip status={d.status} />
        {d.engine && <Chip>{L.lv.engine} {d.engine}</Chip>}
      </div>
      {t && <div className="mt-3 text-t4 font-semibold leading-snug"><span className="mr-1 font-medium text-ink-3">#{t.id}</span>{t.title}</div>}
      {(model || d.model || tk) && (
        <div className="mt-1">
          {(model || d.model) && <Kv k={L.lv.model} v={model || d.model} />}
          {tk && <Kv k={L.lv.tokens} v={tk} />}
        </div>
      )}
      <Uses id={id} lib={lib} />
      <Label>{L.lv.process}</Label>
      {p ? (
        <>
          <div className="flex items-center gap-2 text-t4 font-semibold">{p.type + (p.version ? ' v' + p.version : '')}<Chip>{p.status || '—'}</Chip></div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {p.stages.map(s => (
              <span key={String(s.n)} title={L.lv.stage + ' ' + s.n}
                className={`rounded-chip px-2 py-1 text-t6 font-semibold ${String(s.n) === String(p.now) ? 'bg-blue-weak text-blue-ink' : 'bg-grey-weak text-grey-ink'}`}>{s.n} {s.name}</span>
            ))}
          </div>
          {p.now && <div className="mt-2 text-t6 text-ink-3">{L.lv.stage} {p.now}</div>}
        </>
      ) : <Empty>{L.lv.noProc}</Empty>}
      {f && (
        <>
          <Label>{L.lv.fixing}</Label>
          <div className="rounded-box bg-blue-weak px-5 py-4 text-t5 leading-relaxed">
            {[(L.lv.fixBy as Record<string, string>)[f.by] || f.by, f.type, f.stage ? fmt(L.lv.stageN, { n: f.stage }) : '', '#' + f.task_id].filter(Boolean).join(' · ')}
          </div>
        </>
      )}
      <Label>{L.lv.skills}</Label>
      {d.skills.length
        ? <div className="flex flex-wrap gap-1.5">{d.skills.map(s => <span key={s.name} title={s.found ? '' : L.lv.missing}><Chip tone={s.found ? 'grey' : 'orange'}>{s.name}</Chip></span>)}</div>
        : <Empty>{L.lv.noSkills}</Empty>}
      <Label>{L.lv.week}</Label>
      <DaysChart days={d.days} />
      <Label>{L.lv.recent}</Label>
      {d.recent.length ? (
        <div>
          {d.recent.map(r => (
            <div key={r.id} className={ROW}>
              <div className="min-w-0 flex-1">
                <div className="text-t5 font-semibold leading-snug [overflow-wrap:anywhere]">#{r.id} {r.title}</div>
                <div className="mt-0.5 text-t6 text-ink-3">{[(L.task as Record<string, string>)[r.state] || r.state, [r.model, r.effort].filter(Boolean).join(' · '), tokens(r.tokens)].filter(Boolean).join(' · ')}</div>
              </div>
            </div>
          ))}
        </div>
      ) : <Empty>{L.lv.none}</Empty>}
    </>
  );
}

export function laneSheet({ id, label, d, lib }: { id: string; label: string; d: LaneDetail | null; lib: ProcLib | null }): SheetContent {
  return {
    id: 'l-' + id, label,
    head: <SheetHead onClose={closeItem}><div className="min-w-0 flex-1 truncate text-t3 font-bold leading-snug">{label}</div></SheetHead>,
    body: d ? <Body id={id} d={d} lib={lib} /> : <SkelLines />,
  };
}
