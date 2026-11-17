import { useState } from 'react';
import { CardList, ItemRow } from '../components/ItemCard';
import { Icon } from '../components/Icon';
import { StatusDot } from '../components/lane/StatusChip';
import { ReachCard } from '../components/ReachCard';
import { Section } from '../components/Section';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { forHuman, isOpen, newest } from '../lib/items';
import type { BoardState, Lane } from '../lib/types';

function Doing({ l }: { l: Lane }) {
  const { L } = useL();
  const text = l.doing || (l.task_title ? `#${l.task_id} ${l.task_title}` : '');
  const live = l.status === 'busy' || l.status === 'asking';
  return (
    <div className={`mb-6 flex items-start gap-3 rounded-card px-5 py-4 ${live ? 'bg-blue-weak' : 'bg-surface'}`}>
      <span className="mt-2"><StatusDot status={l.status} /></span>
      <div className="min-w-0">
        <div className={`text-t6 font-semibold ${l.status === 'busy' ? 'text-blue-ink' : 'text-ink-3'}`}>{l.label || l.lane} · {(L.status as Record<string, string>)[l.status] || l.status}</div>
        <div className={`text-t5 leading-normal ${text ? 'font-semibold' : 'text-ink-3'}`}>{text || L.label.noTask}</div>
      </div>
    </div>
  );
}

function Handling({ list }: { list: BoardState['items'] }) {
  const { L } = useL();
  const [open, setOpen] = useState(false);
  return (
    <section className="mb-8 overflow-hidden rounded-card bg-surface">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex min-h-14 w-full items-center gap-2 px-5 text-left text-t5 font-semibold text-ink-2">
        <span className="flex-1">{fmt(L.sec.handling, { n: list.length })}</span>
        <Icon name="chev" className={`size-5 text-ink-4 transition-transform ${open ? '-rotate-90' : 'rotate-90'}`} />
      </button>
      {open && list.map(it => <ItemRow key={it.id} it={it} />)}
    </section>
  );
}

export function HubView({ d }: { d: BoardState }) {
  const { L } = useL();
  const mine = d.items.filter(forHuman);
  const asks = mine.filter(x => x.question).sort(newest), rest = mine.filter(x => !x.question).sort(newest);
  const side = d.items.filter(it => isOpen(it) && it.audience === 'hub').sort(newest);
  const hub = d.lanes.find(l => l.lane === 'hub');
  return (
    <>
      {hub && <Doing l={hub} />}
      {asks.length > 0 && <Section title={L.sec.asks} n={asks.length}><CardList list={asks} empty="" /></Section>}
      <Section title={L.sec.yours} n={rest.length}><CardList list={rest} empty={L.empty.yours} /></Section>
      {side.length > 0 && <Handling list={side} />}
      {d.local_only && <ReachCard />}
      <XSections tab="hub" />
    </>
  );
}
