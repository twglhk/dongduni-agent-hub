import { useL } from '../../i18n/context';
import { Chip, type Tone } from '../Chips';

const TONE: Record<string, Tone> = { busy: 'blue', asking: 'orange', unknown: 'red' };
const DOT: Record<string, string> = {
  busy: 'bg-blue animate-[beat_1.6s_ease-in-out_infinite]', asking: 'bg-orange', idle: 'bg-ink-4',
  closed: 'shadow-[inset_0_0_0_1.5px_var(--text-4)]', unknown: 'shadow-[inset_0_0_0_1.5px_var(--red)]',
};

export const StatusDot = ({ status }: { status: string }) =>
  <i className={`inline-block size-2 flex-none rounded-full ${DOT[status] || 'bg-ink-4'}`} />;

export function StatusChip({ status }: { status: string }) {
  const { L } = useL();
  return (
    <Chip tone={TONE[status]}>
      <StatusDot status={status} />
      <span className={status === 'closed' ? 'text-ink-3' : ''}>{(L.status as Record<string, string>)[status] || status}</span>
    </Chip>
  );
}
