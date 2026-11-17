import { useL } from '../i18n/context';
import { fmt } from '../lib/format';
import type { MyHubState } from '../lib/types';

const STEPS = ['s1', 's2', 's3', 's4'] as const;

export function HubWait({ w }: { w: MyHubState }) {
  const { L } = useL();
  const full = w.state === 'full';
  const spin = `mb-4 size-7 rounded-full border-[3px] animate-[spin-slow_1s_linear_infinite] ${full ? 'border-orange-weak border-t-orange [animation-duration:2.4s]' : 'border-blue-weak border-t-blue'}`;
  const keep = `mt-5 rounded-tile px-4 py-3 text-t6 leading-normal ${full ? 'bg-orange-weak text-orange-ink' : 'bg-blue-weak text-blue-ink'}`;
  if (full) {
    return (
      <div role="status" className="mb-6 rounded-card bg-surface px-5 py-6">
        <div className={spin} aria-hidden="true" />
        <div className="text-t3 font-bold leading-snug">{L.hw.fullTitle}</div>
        <div className="mt-2 text-t5 leading-relaxed text-ink-2">{fmt(L.hw.fullSub, { c: w.cap ?? 0, p: w.place ?? 0 })}</div>
        <div className={keep}>{L.hw.fullKeep}</div>
      </div>
    );
  }
  const at = w.step === 'reading' ? 2 : 1;
  return (
    <div role="status" aria-live="polite" className="mb-6 rounded-card bg-surface px-5 py-6">
      <div className={spin} aria-hidden="true" />
      <div className="text-t3 font-bold leading-snug">{L.hw.title}</div>
      <div className="mt-2 text-t5 leading-relaxed text-ink-2">{L.hw.sub}</div>
      <ol className="mt-5 list-none p-0">
        {STEPS.map((k, i) => (
          <li key={k} className={`flex items-center gap-3 py-1.5 text-t5 ${i < at ? 'text-ink-2' : i === at ? 'font-semibold text-ink' : 'text-ink-4'}`}>
            <i className={`size-2.5 flex-none rounded-full ${i < at ? 'bg-green' : i === at ? 'animate-[beat_1.2s_infinite] bg-blue' : 'bg-line-2'}`} />
            {L.hw[k]}
          </li>
        ))}
      </ol>
      <div className={keep}>{L.hw.keep}</div>
    </div>
  );
}
