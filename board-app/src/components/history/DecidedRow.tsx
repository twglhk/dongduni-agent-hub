import { useL } from '../../i18n/context';
import { fmtT } from '../../lib/format';
import { laneLabel } from '../../lib/items';
import type { Item } from '../../lib/types';
import { useBoard } from '../../state/board';
import { openItem } from '../../state/route';
import { Chip, VERDICT_TONE } from '../Chips';
import { Icon } from '../Icon';
import { verdictLabel } from '../item/Verdict';

// A card a person answered: the answer, the card, and who answered when; it opens the card.
export function DecidedRow({ it }: { it: Item }) {
  const { L } = useL();
  const { data } = useBoard();
  return (
    <button type="button" onClick={() => openItem(it.id)}
      className="pressable flex min-h-16 w-full items-center gap-3 rounded-box bg-surface px-4 py-3 text-left">
      <Chip tone={VERDICT_TONE[it.verdict || ''] || 'grey'}>{verdictLabel(L, it) || it.state}</Chip>
      <div className="min-w-0 flex-1">
        <div className="text-t5 font-semibold leading-snug [overflow-wrap:anywhere]">{it.title}</div>
        <div className="mt-0.5 truncate text-t6 text-ink-3">{[laneLabel(data, it.lane), it.choice, it.decided_by, fmtT(it.decided)].filter(Boolean).join(' · ')}</div>
      </div>
      <Icon name="chev" className="size-5 text-ink-4" />
    </button>
  );
}
