import { useL } from '../../i18n/context';
import { fmt, fmtT } from '../../lib/format';
import type { SessionView, SessionWork } from '../../lib/laneTypes';
import { closeItem } from '../../state/route';
import { Chip } from '../Chips';
import { Prose } from '../Prose';
import { StatusChip } from '../lane/StatusChip';
import { Empty } from '../Section';
import { SheetHead, type SheetContent } from '../Sheet';
import { SkelLines } from '../Skeleton';

function Label({ children }: { children: string }) {
  return <h3 className="mt-6 mb-2 text-t6 font-semibold text-ink-3 first:mt-3">{children}</h3>;
}

function Work({ w }: { w: SessionWork }) {
  const { L } = useL();
  const kind = fmt((L.ln.kinds as Record<string, string>)[w.kind] || L.ln.kinds.task, { n: w.id });
  const meta = [w.from && fmt(L.ln.by, { who: w.from }), w.request && fmt(L.ln.requestN, { n: w.request }),
    w.started && fmt(L.ln.startedAt, { t: fmtT(w.started) }), w.ended && fmt(L.ln.endedAt, { t: fmtT(w.ended) })].filter(Boolean);
  return (
    <>
      <div className="flex flex-wrap items-center gap-2"><Chip tone={w.live ? 'blue' : 'grey'}>{kind}</Chip>{!w.live && <Chip>{L.ln.last}</Chip>}</div>
      <div className="mt-2 text-t4 font-semibold leading-snug [overflow-wrap:anywhere]">{w.title}</div>
      {meta.length > 0 && <div className="mt-1 text-t6 text-ink-3">{meta.join(' · ')}</div>}
      <Label>{L.ln.fullText}</Label>
      <Prose text={w.body} className="text-t5 [overflow-wrap:anywhere]" />
    </>
  );
}

function Body({ v }: { v: SessionView }) {
  const { L } = useL();
  return (
    <>
      {v.hidden && <Empty>{L.ln.hidden}</Empty>}
      {!v.hidden && !v.work && <Empty>{L.ln.noWork}</Empty>}
      {v.work && <Work w={v.work} />}
      {v.report && (
        <>
          <Label>{L.ln.newestReport}</Label>
          <div className="rounded-box bg-surface-3 px-4 py-3 text-t5 leading-relaxed">
            <span className="mr-2 text-t6 font-semibold text-ink-3">{(L.report as Record<string, string>)[v.report.kind] || v.report.kind}</span>{v.report.text}
          </div>
        </>
      )}
    </>
  );
}

export function sessionSheet({ key, label, v, status }: { key: string; label: string; v: SessionView | null; status?: string }): SheetContent {
  return {
    id: 's-' + key, label,
    head: (
      <SheetHead onClose={closeItem}>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <div className="min-w-0 truncate text-t3 font-bold leading-snug">{label}</div>
          {status && <StatusChip status={status} />}
        </div>
      </SheetHead>
    ),
    body: v ? <Body v={v} /> : <SkelLines />,
  };
}
