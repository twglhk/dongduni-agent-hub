import type { MapEntry } from '../../lib/engineTypes';
import { Section } from '../Section';

export function EntryList({ title, list, status }: { title: string; list: MapEntry[]; status?: boolean }) {
  if (!list.length) return null;
  return (
    <Section title={title} n={list.length}>
      <div className="overflow-hidden rounded-card bg-surface">
        {list.map(x => (
          <div key={x.name} className="border-line px-5 py-3 [&+&]:border-t">
            <div className="text-t5 font-semibold">{x.name}{status && x.status ? ' · ' + x.status : ''}</div>
            {x.note && <div className="mt-0.5 text-t6 text-ink-3">{x.note}</div>}
          </div>
        ))}
      </div>
    </Section>
  );
}
