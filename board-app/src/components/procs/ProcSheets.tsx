import type { ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { frameWord, fullTone, fullWord, inkOf, plain, procDocUrl, statusTone, statusWord } from '../../lib/procs';
import type { FullProcess, ProcLib, SubProcess } from '../../lib/procTypes';
import { openDoc } from '../../state/docSheet';
import { closeItem, openFull, openLane } from '../../state/route';
import { Chip } from '../Chips';
import { Label } from '../lane/LaneSheet';
import { SheetHead, type SheetContent } from '../Sheet';
import { SkelLines } from '../Skeleton';
import { Flow } from './Flow';
import { Verification } from './Verification';
import { Row } from './Row';

type LaneName = (id: string) => string;

function Head({ title, kind, status, chip }: { title: string; kind: string; status: string; chip?: ReactNode }) {
  const { L } = useL();
  return (
    <SheetHead onClose={closeItem}>
      <div className="min-w-0 flex-1">
        <div className="text-t3 font-bold leading-snug break-words">{title}</div>
        <div className="mt-1 flex items-center gap-2 text-t5 text-ink-3">
          <span>{kind}</span>{chip || <Chip tone={statusTone(status)}>{statusWord(L, status)}</Chip>}
        </div>
      </div>
    </SheetHead>
  );
}

function Lanes({ ids, lane }: { ids: string[]; lane: LaneName }) {
  const { L } = useL();
  if (!ids.length) return <Row title={L.pl.noLane} sub={L.pl.noLaneSub} />;
  return <>{ids.map(id => <Row key={id} title={lane(id)} onClick={() => openLane(id)} label={fmt(L.pl.open, { name: lane(id) })} />)}</>;
}

function DocRow({ title, path }: { title: string; path: string }) {
  return <Row title={title} sub={path} onClick={() => openDoc({ title, path, url: procDocUrl(path) })} />;
}

export function fullSheet(f: FullProcess, v: ProcLib, lane: LaneName): SheetContent {
  return {
    id: 'full-' + f.id, label: f.title,
    head: <FullHead f={f} v={v} />,
    body: <FullBody f={f} v={v} lane={lane} />,
  };
}

function FullHead({ f, v }: { f: FullProcess; v: ProcLib }) {
  const { L } = useL();
  return <Head title={f.title} kind={L.pl.kindFull} status={f.status} chip={<Chip tone={fullTone(v, f)}>{fullWord(L, v, f)}</Chip>} />;
}

function FullBody({ f, v, lane }: { f: FullProcess; v: ProcLib; lane: LaneName }) {
  const { L } = useL();
  const P = L.pl;
  return (
    <>
      <Label>{P.deliverable}</Label>
      <div className="py-1 text-t5 leading-normal break-words">{plain(f.deliverable) || '—'}</div>
      {f.frame_exception && <div className="mt-1 text-t6 text-ink-3">{fmt(P.exception, { why: f.frame_exception.replace(/^exception\s*[—-]\s*/i, '') })}</div>}
      <Verification f={f} v={v} />
      <Label>{P.flow}</Label>
      <Flow f={f} v={v} />
      <Label>{P.lanes}</Label>
      <Lanes ids={f.lanes} lane={lane} />
      <Label>{P.docs}</Label>
      <DocRow title={P.fullFile} path={f.file} />
    </>
  );
}

export function subSheet(s: SubProcess, v: ProcLib, lane: LaneName): SheetContent {
  return { id: 'sub-' + s.id, label: s.title, head: <SubHead s={s} />, body: <SubBody s={s} v={v} lane={lane} /> };
}

function SubHead({ s }: { s: SubProcess }) {
  const { L } = useL();
  return <Head title={s.title} kind={L.pl.kindSub + (s.version ? ' · v' + s.version : '')} status={s.confirmed ? 'confirmed' : s.status} />;
}

function SubBody({ s, v, lane }: { s: SubProcess; v: ProcLib; lane: LaneName }) {
  const { L } = useL();
  const P = L.pl;
  const own = s.skills.map(k => k.name).join(',');
  return (
    <>
      <Label>{P.stages} {s.stages.length}</Label>
      {s.stages.map(x => {
        const extra = x.skills.map(k => k.name).join(',') !== own ? x.skills.map(k => k.name).join(', ') : '';
        const pat = x.pattern?.exists ? x.pattern : null;
        return (
          <Row key={x.n} title={`${x.n} · ${x.name}`} sub={plain(x.output)}
            meta={extra ? P.skillOf + ' ' + extra : undefined}
            right={<span className={`flex-none text-t6 ${pat ? inkOf(pat.color) : 'text-ink-3'}`}>{pat ? statusWord(L, pat.status) : P.noPattern}</span>}
            onClick={pat ? () => openDoc({ title: `${x.n} · ${x.name}`, path: pat.path, url: procDocUrl(pat.path) }) : undefined} />
        );
      })}
      <Label>{P.skills}</Label>
      {s.skills.map(k => (
        <Row key={k.name} title={k.name} sub={k.doc || P.skillMissing}
          onClick={k.doc ? () => openDoc({ title: P.skillOf + ' · ' + k.name, path: k.doc!, url: procDocUrl(k.doc!) }) : undefined} />
      ))}
      <Label>{P.fulls}</Label>
      {s.used_by_full.map(fid => {
        const f = v.full.find(x => x.id === fid);
        const steps = f?.steps.filter(st => st.sub === s.id) || [];
        return <Row key={fid} title={f?.title || fid} onClick={() => openFull(fid)} label={fmt(P.open, { name: f?.title || fid })}
          sub={steps.map(st => `${P.kindSub} ${st.n} · ${frameWord(L, st.frame)}`).join(', ')} />;
      })}
      <Label>{P.lanes}</Label>
      <Lanes ids={s.used_by_lanes} lane={lane} />
      <Label>{P.docs}</Label>
      <DocRow title={P.card} path={s.file} />
    </>
  );
}

export function loadingSheet(id: string): SheetContent {
  return { id, label: '', head: <SheetHead onClose={closeItem}><div className="flex-1" /></SheetHead>, body: <SkelLines /> };
}
