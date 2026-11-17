import { useL } from '../../i18n/context';
import type { EngineMap } from '../../lib/engineTypes';
import { fmt, fmtT } from '../../lib/format';
import { Empty, Section } from '../Section';

export function Changes({ ch }: { ch: EngineMap['changes'] }) {
  const { L } = useL();
  const E = L.eg as Record<string, string>;
  return (
    <Section title={E.changes} n={ch.items.length || undefined}>
      {!ch.since ? <Empty>{E.noSnap}</Empty> : (
        <div className="grid gap-2">
          <div className="px-1 text-t6 text-ink-3">{fmt(E.since, { d: fmtT(ch.since) })}</div>
          {ch.items.length ? (
            <div className="overflow-hidden rounded-card bg-surface">
              {ch.items.map((c, i) => <div key={i} className="border-line px-5 py-3 text-t5 [&+&]:border-t">
                {fmt(E['c_' + c.kind] || '{name}', { name: c.name, a: c.from || E.none, b: c.to || E.none })}</div>)}
            </div>
          ) : <Empty>{E.same}</Empty>}
        </div>
      )}
    </Section>
  );
}
