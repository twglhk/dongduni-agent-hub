import { useEffect, useState } from 'react';
import { DaysChart } from '../components/DaysChart';
import { XSections } from '../components/XHost';
import { Changes } from '../components/engine/Changes';
import { EngineSkills } from '../components/engine/EngineSkills';
import { EntryList } from '../components/engine/EntryList';
import { PipeCard } from '../components/engine/PipeCard';
import { Seg } from '../components/Seg';
import { SkelCards } from '../components/Skeleton';
import { Empty, Section } from '../components/Section';
import { useL } from '../i18n/context';
import type { EngineInfo, EngineMap } from '../lib/engineTypes';
import { pickEngine } from '../state/route';
import { usePoll } from '../state/usePoll';

const MS = 5000;
const hashEngine = () => /^#engine-(.+)$/.exec(location.hash)?.[1] || null;

// Tokens per day, processes with their stages and cards, the skills that write them down, then what changed.
export function EngineView() {
  const { L } = useL();
  const list = usePoll<{ engines: EngineInfo[] }>('api/engines', MS);
  const [want, setWant] = useState(hashEngine);
  useEffect(() => {
    const on = () => { const h = hashEngine(); if (h) setWant(decodeURIComponent(h)); };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const engs = list.data?.engines || null;
  const cur = engs ? (engs.find(e => e.id === want) || engs[0])?.id || null : null;
  const map = usePoll<EngineMap>(cur ? `api/engines/${encodeURIComponent(cur)}/map` : null, MS);
  const E = L.eg;
  if (!engs || (cur && !map.data)) return <SkelCards n={2} rows={4} />;
  if (!cur) return <Empty>{E.empty}</Empty>;
  const d = map.data!;
  return (
    <>
      {engs.length > 1 && <Seg opts={engs.map(e => ({ id: e.id, label: e.label }))} cur={cur} onPick={pickEngine} />}
      {!d.implicit && <h2 className="mb-3 px-1 text-t2 font-bold">{d.label}</h2>}
      {d.notes && <div className="mb-4 px-1 text-t6 leading-normal text-ink-3">{d.notes}</div>}
      <Section title={E.usage}><div className="rounded-card bg-surface px-5 py-4"><DaysChart days={d.usage} /></div></Section>
      <Section title={E.pipes} n={d.pipelines.length}>
        {d.pipelines.length ? <div className="grid grid-cols-1 gap-3">{d.pipelines.map(p => <PipeCard key={p.row} eid={d.id} p={p} />)}</div> : <Empty>{E.noRows}</Empty>}
      </Section>
      <EngineSkills d={d} />
      <EntryList title={E.tools} list={d.tools} />
      <EntryList title={E.exps} list={d.experiments} status />
      <Changes ch={d.changes} />
      <XSections tab="engine" />
    </>
  );
}
