import { useState } from 'react';
import { CardList, ItemRow } from '../components/ItemCard';
import { Icon } from '../components/Icon';
import { StatusDot } from '../components/lane/StatusChip';
import { HubWait } from '../components/HubWait';
import { ReachCard } from '../components/ReachCard';
import { PrefsTip } from '../components/prefs/PrefsTip';
import { Empty, Section } from '../components/Section';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import { forHuman, forMe, isOpen, myHub, newest } from '../lib/items';
import { isHidden, ordered } from '../lib/prefs';
import type { BoardState, Lane } from '../lib/types';
import { useFold, usePrefs } from '../state/prefs';

function Doing({ l }: { l: Lane }) {
  const { L } = useL();
  const text = l.hidden ? L.label.otherWork : l.doing || (l.task_title ? `#${l.task_id} ${l.task_title}` : '');
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
  const mine = d.items.filter(forHuman).filter(it => forMe(d, it));
  const asks = mine.filter(x => x.question).sort(newest), rest = mine.filter(x => !x.question).sort(newest);
  const side = d.items.filter(it => isOpen(it) && it.audience === 'hub' && forMe(d, it)).sort(newest);
  const hub = d.lanes.find(l => l.lane === myHub(d)) || d.lanes.find(l => l.lane === 'hub');
  const w = d.my_hub_state;
  const { p } = usePrefs();
  const fold = useFold();
  const secs = { asks: { title: L.sec.asks, list: asks, empty: '' }, yours: { title: L.sec.yours, list: rest, empty: L.empty.yours } };
  const shown = ordered(p, 'hub').filter(id => !isHidden(p, 'hub', id) && (id !== 'asks' || asks.length > 0));
  return (
    <>
      {w && (w.state === 'starting' || w.state === 'full') && <HubWait w={w} />}
      {hub && <Doing l={hub} />}
      {!shown.length && (
        <section className="mb-8">
          <div className="mb-3 flex min-h-7 items-center px-1"><PrefsTip /></div>
          <Empty>{L.pf.allHidden}</Empty>
        </section>
      )}
      {shown.map((id, i) => (
        <Section key={id} title={secs[id].title} n={secs[id].list.length} fold={fold('hub', id)} aside={i === 0 ? <PrefsTip /> : undefined}>
          <CardList list={secs[id].list} empty={secs[id].empty} />
        </Section>
      ))}
      {side.length > 0 && <Handling list={side} />}
      {d.local_only && <ReachCard />}
      <XSections tab="hub" />
    </>
  );
}
