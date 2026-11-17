import { useL } from '../../i18n/context';
import { Icon } from '../Icon';

export function LaneAdd({ onOpen }: { onOpen: () => void }) {
  const { L } = useL();
  return (
    <button type="button" aria-haspopup="dialog" onClick={onOpen}
      className="mt-6 flex min-h-14 w-full items-center gap-2 rounded-card bg-surface px-5 text-left text-t5 font-semibold text-blue-ink active:bg-[var(--press)]">
      <Icon name="plus" /><span className="flex-1">{L.nl.add}</span>
      <Icon name="chev" className="size-5 text-ink-4" />
    </button>
  );
}
