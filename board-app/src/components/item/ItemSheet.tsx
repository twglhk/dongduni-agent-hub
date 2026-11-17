import { useL } from '../../i18n/context';
import { HttpError, post } from '../../lib/api';
import { isOpen } from '../../lib/items';
import type { Item } from '../../lib/types';
import { useBoard } from '../../state/board';
import { dropDraft, setDraft, useDraft } from '../../state/drafts';
import { closeItem } from '../../state/route';
import { useToast } from '../../state/toast';
import { Chip, KindChip, LaneChip, Meta, When } from '../Chips';
import { Prose } from '../Prose';
import { SheetHead, type SheetContent } from '../Sheet';
import { Answer, type Verdict as Choice } from './Answer';
import { Attachments } from './Attachments';
import { H3 } from './H3';
import { Options } from './Options';
import { Verdict } from './Verdict';

const canAct = (it: Item) => it.state === 'open' && it.audience === 'human';

function Box({ tone, label, text }: { tone: 'q' | 'esc'; label: string; text: string }) {
  const c = tone === 'q' ? ['bg-orange-weak', 'text-orange-ink'] : ['bg-blue-weak', 'text-blue-ink'];
  return (
    <div className={`mb-3 whitespace-pre-wrap rounded-box px-5 py-4 text-t5 leading-relaxed ${c[0]}`}>
      <span className={`mb-1 block text-t6 font-semibold ${c[1]}`}>{label}</span>{text}
    </div>
  );
}

function Head({ it }: { it: Item }) {
  const { L } = useL();
  return (
    <SheetHead onClose={closeItem}>
      <Meta className="flex-1">
        <LaneChip lane={it.lane} /><KindChip it={it} />
        {isOpen(it) && it.audience === 'hub' && <Chip>{L.label.hubSide}</Chip>}
        <span>#{it.id}</span><When at={it.created} className="ml-auto" />
      </Meta>
    </SheetHead>
  );
}

function Body({ it, onImage }: { it: Item; onImage: (imgs: string[], i: number) => void }) {
  const { L } = useL();
  const act = canAct(it), d = useDraft(it.id);
  const picks = act ? (it.multi ? d.picks : d.choice ? [d.choice] : []) : null;
  const pick = (label: string) => {
    if (!it.multi) return setDraft(it.id, { choice: d.choice === label ? null : label });
    const set = new Set(d.picks);
    if (set.has(label)) set.delete(label); else set.add(label);
    setDraft(it.id, { picks: it.options.map(o => o.label).filter(l => set.has(l)) });
  };
  return (
    <>
      <h2 className="mt-2 mb-4 text-t2 font-bold leading-[1.35] tracking-[-.01em]">{it.title}</h2>
      {!isOpen(it) && <Verdict it={it} />}
      {it.question && <Box tone="q" label={L.label.question} text={it.question} />}
      {it.escalation && <Box tone="esc" label={L.label.why} text={it.escalation} />}
      {it.body && <Prose text={it.body} />}
      {it.attachments.length > 0 && <Attachments paths={it.attachments} onImage={onImage} />}
      {it.kind === 'decision' && it.options.length > 0 && <Options it={it} picks={picks} busy={d.busy} onPick={pick} />}
      {!act && it.note && <>
        <H3>{it.verdict === 'answer' ? L.label.answer : L.label.note}</H3>
        <div className="rounded-box bg-surface-3 px-5 py-4"><Prose text={it.note} className="small" /></div>
      </>}
    </>
  );
}

function Cta({ it }: { it: Item }) {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const d = useDraft(it.id);
  const picked = it.multi ? d.picks.length : d.choice ? 1 : 0;
  const decide = async (verdict: Choice) => {
    if (d.busy) return;
    const body: Record<string, unknown> = { verdict, note: d.note.trim() };
    if (verdict === 'ok' && it.kind === 'decision') { if (it.multi) body.choices = d.picks; else body.choice = d.choice; }
    setDraft(it.id, { busy: true });
    try {
      await post(`api/items/${it.id}/decide`, body);
      dropDraft(it.id);
      toast(verdict === 'hold' ? L.msg.held : verdict === 'answer' ? L.msg.answered : it.kind === 'review' ? L.msg.reviewed : L.msg.decided);
      closeItem();
      refresh();
    } catch (e) {
      setDraft(it.id, { busy: false });
      if ((e as HttpError).status !== 401) toast((e as Error).message, true);
    }
  };
  return <Answer it={it} note={d.note} setNote={note => setDraft(it.id, { note })} picked={picked} busy={d.busy} decide={decide} />;
}

export function itemSheet(it: Item, onImage: (imgs: string[], i: number) => void): SheetContent {
  return {
    id: 'i' + it.id, label: it.title,
    head: <Head it={it} />,
    body: <Body it={it} onImage={onImage} />,
    cta: canAct(it) ? <Cta it={it} /> : undefined,
  };
}
