import { useL } from '../../i18n/context';
import { pipeTitle } from '../../lib/engine';
import type { EngineMap } from '../../lib/engineTypes';
import { Meta } from '../Chips';
import { Empty, Section } from '../Section';
import { DocChip } from './DocChip';

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5"><div className="text-t6 font-semibold text-ink-3">{title}</div><Meta>{children}</Meta></div>;
}

export function EngineSkills({ d }: { d: EngineMap }) {
  const { L } = useL();
  const n = d.pipelines.length + d.skills.length;
  const own = d.skills.filter(s => !s.shared), shared = d.skills.filter(s => s.shared);
  return (
    <Section title={L.eg.skills} n={n}>
      {n ? (
        <div className="grid gap-3 rounded-card bg-surface px-5 py-4">
          {d.pipelines.map(p => (
            <Group key={p.row} title={'#' + p.row + ' ' + pipeTitle(p)[0]}>
              <DocChip eid={d.id} path={p.card} label={L.eg.procCard} />
            </Group>
          ))}
          {own.length > 0 && <Group title={L.eg.folder}>{own.map(s => <DocChip key={s.name} eid={d.id} path={s.doc} label={s.name} />)}</Group>}
          {shared.length > 0 && <Group title={L.eg.shared}>{shared.map(s => <DocChip key={s.name} eid={d.id} path={s.doc} label={s.name} />)}</Group>}
        </div>
      ) : <Empty>{L.lv.none}</Empty>}
    </Section>
  );
}
