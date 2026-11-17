import { useL } from '../../i18n/context';
import { fmtT } from '../../lib/format';
import type { Item } from '../../lib/types';

const INK: Record<string, string> = { ok: 'text-green-ink', no: 'text-red-ink', hold: 'text-orange-ink', answer: 'text-blue-ink' };

export function verdictLabel(L: ReturnType<typeof useL>['L'], it: Item) {
  if (!it.verdict) return it.state === 'merged' ? L.verdict.merged : '';
  return it.verdict === 'ok' && it.kind === 'review' ? L.verdict.okReview : (L.verdict as Record<string, string>)[it.verdict] || it.verdict;
}

export function Verdict({ it }: { it: Item }) {
  const { L } = useL();
  return (
    <div className="mb-3 flex items-center gap-3 rounded-box bg-surface-3 px-5 py-4">
      <div className="min-w-0">
        <div className={`text-t3 font-bold leading-tight ${INK[it.verdict || ''] || ''}`}>{verdictLabel(L, it)}</div>
        {it.choice && <div className="mt-0.5 text-t5 font-semibold">{it.choice}</div>}
      </div>
      <div className="ml-auto flex-none text-right text-t6 leading-normal text-ink-3">
        {it.decided_by}{it.decided_by && <br />}{fmtT(it.decided)}
      </div>
    </div>
  );
}
