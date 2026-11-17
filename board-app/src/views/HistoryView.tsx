import { useState } from 'react';
import { DecidedRow } from '../components/history/DecidedRow';
import { MemoRow } from '../components/history/MemoRow';
import { ReportCard } from '../components/history/ReportCard';
import { Seg } from '../components/Seg';
import { SkelCards } from '../components/Skeleton';
import { Empty } from '../components/Section';
import { TaskRow } from '../components/TaskRow';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import type { BoardState, Item, Memo, Report, Task } from '../lib/types';
import { useToast } from '../state/toast';
import { useHistory } from '../state/useHistory';

const KINDS = ['decided', 'reports', 'closed', 'memos', 'requests'] as const;
type Kind = (typeof KINDS)[number];
type Row = Item | Report | Task | Memo;

function Rows({ kind, rows, onImage }: { kind: Kind; rows: Row[]; onImage: (imgs: string[], i: number) => void }) {
  if (kind === 'reports') return <div className="grid grid-cols-1 gap-2">{(rows as Report[]).map(r => <ReportCard key={r.id} r={r} onImage={onImage} />)}</div>;
  if (kind === 'memos' || kind === 'requests') return <div className="grid grid-cols-1 gap-2">{(rows as Memo[]).map(m => <MemoRow key={m.id} m={m} />)}</div>;
  if (kind === 'closed') return <div className="overflow-hidden rounded-card bg-surface">{(rows as Task[]).map(t => <TaskRow key={t.id} t={t} />)}</div>;
  return <div className="grid grid-cols-1 gap-2 [&>*]:min-w-0">{(rows as Item[]).map(it => <DecidedRow key={it.id} it={it} />)}</div>;
}

export function HistoryView({ d, onImage }: { d: BoardState; onImage: (imgs: string[], i: number) => void }) {
  const { L } = useL();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>('decided');
  const h = useHistory<Row>(kind, m => toast(m, true));
  return (
    <>
      <Seg opts={KINDS.map(k => ({ id: k, label: L.hist[k], n: d.counts[k] }))} cur={kind} onPick={id => setKind(id as Kind)} />
      {!h.rows.length ? h.loaded ? <Empty>{L.empty.hist}</Empty> : <SkelCards /> : <Rows kind={kind} rows={h.rows} onImage={onImage} />}
      {h.loaded && h.rows.length > 0 && (h.more
        ? <button type="button" disabled={h.busy} onClick={h.loadMore}
            className="mt-3 flex min-h-12 w-full items-center justify-center rounded-card bg-surface text-t5 font-semibold text-ink-2 disabled:text-ink-4">
            {h.busy ? L.empty.loading : L.btn.more}</button>
        : <div className="py-6 text-center text-t5 text-ink-3">{L.empty.end}</div>)}
      <XSections tab="history" />
    </>
  );
}
