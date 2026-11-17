import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { checkTone, showsVerification } from '../../lib/procs';
import type { FullProcess, ProcLib } from '../../lib/procTypes';
import { openItem } from '../../state/route';
import { Chip } from '../Chips';
import { Label } from '../lane/LaneSheet';
import { Row } from './Row';

export function Verification({ f, v }: { f: FullProcess; v: ProcLib }) {
  const { L } = useL();
  const V = L.vf;
  if (!showsVerification(v, f)) return null;
  const rec = f.verified;
  const head = f.reverify
    ? { title: V.reverify, sub: fmt(V.reverifySub, { what: (f.changes || []).slice(0, 3).join(', ') }) }
    : f.status === 'verified' && rec
      ? { title: fmt(V.verified, { at: rec.at }), sub: fmt(V.verifiedSub, { card: rec.card, by: rec.by || V.someone }) }
      : f.status === 'confirmed' ? { title: V.confirmed, sub: V.confirmedSub } : { title: V.draft, sub: V.draftSub };
  const run = f.run;
  const ck = run?.check;
  const subTitle = (id: string) => v.sub.find(s => s.id === id)?.title || id;
  return (
    <>
      <Label>{V.title}</Label>
      <Row title={head.title} sub={head.sub} />
      {run && (
        <>
          <Label>{V.run}</Label>
          <Row title={run.key} sub={fmt(V.step, { n: run.n ?? '?', total: f.steps.length, sub: subTitle(run.sub) })}
            meta={ck ? `${subTitle(ck.sub)} · ${fmt(V.cardOf, { n: ck.item_id, who: ck.member || V.someone })}` : undefined}
            right={ck ? <Chip tone={checkTone(ck.state)}>{V.ck[ck.state] || ck.state}</Chip> : undefined}
            onClick={ck ? () => openItem(ck.item_id) : undefined} />
        </>
      )}
    </>
  );
}
