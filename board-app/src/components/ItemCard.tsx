import { useL } from '../i18n/context';
import { fileUrl, fname, IMG } from '../lib/base';
import { rel } from '../lib/format';
import { laneLabel } from '../lib/items';
import type { Item } from '../lib/types';
import { openItem } from '../state/route';
import { useBoard } from '../state/board';
import { KindChip, LaneChip, Meta, When } from './Chips';
import { Icon } from './Icon';
import { Empty } from './Section';

function Thumbs({ paths }: { paths: string[] }) {
  const imgs = paths.filter(p => IMG.test(p));
  if (!imgs.length) return null;
  return (
    <div className="no-scrollbar mt-3 flex gap-2 overflow-x-auto">
      {imgs.slice(0, 3).map(p => <img key={p} src={fileUrl(p)} alt={fname(p)} loading="lazy"
        className="h-[88px] w-auto max-w-40 flex-none rounded-tile bg-media object-cover" />)}
      {imgs.length > 3 && <div className="flex h-[88px] min-w-14 flex-none items-center justify-center rounded-tile bg-surface-3 text-t5 font-semibold text-ink-2">+{imgs.length - 3}</div>}
    </div>
  );
}

export function ItemCard({ it }: { it: Item }) {
  const { L } = useL();
  const lead = it.question || it.escalation;
  return (
    <div role="button" tabIndex={0} onClick={() => openItem(it.id)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openItem(it.id); } }}
      className="pressable block w-full overflow-hidden rounded-card bg-surface px-5 py-4 text-left">
      <Meta><LaneChip lane={it.lane} /><KindChip it={it} /><When at={it.created} className="ml-auto" /></Meta>
      <div className="mt-2 mb-0.5 text-t4 font-bold leading-snug tracking-[-.01em]">{it.title}</div>
      {lead && <div className="mt-0.5 line-clamp-2 text-t5 text-ink-2">{lead}</div>}
      <Thumbs paths={it.attachments} />
      <div className="mt-3 flex min-h-5 items-center gap-3 text-t6 text-ink-3">
        {it.kind === 'decision' && it.options.length > 0 && <span>{L.label.options} {it.options.length}{it.multi ? ` · ${L.label.pickMany}` : ''}</span>}
        {it.attachments.length > 0 && <span>{L.label.attachments} {it.attachments.length}</span>}
        <span className="ml-auto inline-flex text-ink-4"><Icon name="chev" /></span>
      </div>
    </div>
  );
}

export function CardList({ list, empty }: { list: Item[]; empty: string }) {
  if (!list.length) return <Empty>{empty}</Empty>;
  return <div className="grid grid-cols-1 gap-3">{list.map(it => <ItemCard key={it.id} it={it} />)}</div>;
}

export function ItemRow({ it }: { it: Item }) {
  const { L } = useL();
  const { data } = useBoard();
  const kind = it.question ? L.kind.ask : (L.kind as Record<string, string>)[it.kind] || it.kind;
  return (
    <button type="button" onClick={() => openItem(it.id)}
      className="pressable relative flex min-h-16 w-full items-center gap-3 px-5 py-3 text-left before:absolute before:inset-x-5 before:top-0 before:border-t before:border-line">
      <div className="min-w-0 flex-1">
        <div className="text-t5 font-semibold leading-snug [overflow-wrap:anywhere]">{it.title}</div>
        <div className="mt-0.5 truncate text-t6 text-ink-3">{[laneLabel(data, it.lane), kind, rel(L, it.created)].join(' · ')}</div>
      </div>
      <Icon name="chev" className="size-5 text-ink-4" />
    </button>
  );
}
