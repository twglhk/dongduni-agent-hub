import { useState } from 'react';
import { useL } from '../../i18n/context';
import { fileUrl, fname } from '../../lib/base';
import { took } from '../../lib/format';
import type { WorkRow as Work } from '../../lib/types';
import { openItem } from '../../state/route';
import { Chip, LaneChip, Meta, When, type Tone } from '../Chips';
import { Prose } from '../Prose';
import { REPORT_TONE } from './ReportCard';

const TONE: Record<string, Tone> = { queued: 'orange', running: 'blue', wrapping: 'teal', done: 'green', closed: 'green', cancelled: 'red' };
const chip = 'inline-flex min-h-8 items-center rounded-chip bg-surface-3 px-2.5 text-t6 font-semibold text-ink-2 no-underline break-all';

function Sub({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="mt-3"><div className="text-t6 font-semibold text-ink-3">{label}</div><div className="mt-1">{children}</div></div>;
}

function Line({ w }: { w: Work }) {
  const { L } = useL();
  const ln = w.line;
  if (!ln) return null;
  const parts = [ln.full ? [ln.full, ln.sub].filter(Boolean).join(' / ') : `${L.work.oldRow} ${ln.row}`,
    ln.stage ? `${L.work.stage} ${ln.stage}` : '', ln.mode ? (L.work.mode as Record<string, string>)[ln.mode] || ln.mode : ''].filter(Boolean);
  return <Chip tone="blue">{parts.join(' · ')}</Chip>;
}

export function WorkRow({ w }: { w: Work }) {
  const { L } = useL();
  const [open, setOpen] = useState(false);
  const label = w.src === 'task' ? L.work.task : w.src === 'report' ? L.work.report : w.kind === 'decision' ? L.work.decision : L.work.memo;
  const dur = took(L, w.started, w.ended);
  return (
    <div className="rounded-box bg-surface px-5 py-4 text-t5 leading-[1.55]">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="block w-full text-left">
        <Meta><Chip tone={TONE[w.state] || 'grey'}>{(L.work.state as Record<string, string>)[w.state] || w.state}</Chip>
          <Chip>{label}</Chip><LaneChip lane={w.lane} /><Line w={w} /><When at={w.at} className="ml-auto" /></Meta>
        <div className="mt-1.5 font-semibold [overflow-wrap:anywhere]">{w.src !== 'report' && <span className="text-ink-3">#{w.id} </span>}{w.title}</div>
        <Meta className="mt-0.5">{w.by && <span>{L.work.by} {w.by}</span>}{w.requester && <span>{L.work.requester} {w.requester}</span>}{dur && <span>{L.work.took} {dur}</span>}</Meta>
      </button>
      {open && <div>
        {w.brief && <Sub label={L.work.brief}><div className="whitespace-pre-wrap [overflow-wrap:anywhere]">{w.brief}</div></Sub>}
        {w.reports.length > 0 && <Sub label={L.work.reports}>{w.reports.map(r => (
          <div key={r.id} className="mb-2 rounded-tile bg-surface-3 px-4 py-3">
            <Meta><Chip tone={REPORT_TONE[r.kind] || 'grey'}>{(L.report as Record<string, string>)[r.kind] || r.kind}</Chip><When at={r.created} /></Meta>
            <Prose text={r.text} className="chat mt-1" />
            {r.retro && <Meta className="mt-1">{(['cost', 'corrected', 'rule'] as const).filter(k => r.retro?.[k]).map(k => <span key={k}>{L.work[k]}: {r.retro?.[k]}</span>)}</Meta>}
          </div>))}</Sub>}
        {!!w.replies?.length && <Sub label={L.work.replies}>{w.replies.map((r, i) => <div key={i} className="mb-2 rounded-tile bg-blue-weak px-4 py-3"><Prose text={r.text} className="chat" /></div>)}</Sub>}
        {w.cards.length > 0 && <Sub label={L.work.cards}><div className="flex flex-wrap gap-2">{w.cards.map(c =>
          <button key={c.id} type="button" onClick={() => openItem(c.id)} className={chip}>#{c.id} {c.title}</button>)}</div></Sub>}
        {w.commits.length > 0 && <Sub label={L.work.commits}><div className="flex flex-wrap gap-2">{w.commits.map(c => <span key={c} className={`${chip} font-mono`}>{c}</span>)}</div></Sub>}
        {w.files.length > 0 && <Sub label={L.work.files}><div className="flex flex-wrap gap-2">{w.files.map(p => p.endsWith('/')
          ? <span key={p} className={chip}>{p}</span>
          : <a key={p} href={fileUrl(p)} target="_blank" rel="noopener" className={chip}>{fname(p)}</a>)}</div></Sub>}
      </div>}
    </div>
  );
}
