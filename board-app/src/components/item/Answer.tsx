import { useL } from '../../i18n/context';
import { isSendKey, SEND_KEYS } from '../../lib/keys';
import type { Item } from '../../lib/types';
import { Icon } from '../Icon';
import { KbdHint } from '../KbdHint';

export type Verdict = 'ok' | 'answer' | 'hold';

export function Answer({ it, note, setNote, picked, busy, decide }: {
  it: Item; note: string; setNote: (s: string) => void; picked: number; busy: boolean; decide: (v: Verdict) => void;
}) {
  const { L } = useL();
  const decision = it.kind === 'decision';
  const hasNote = !!note.trim();
  const btn = 'flex h-14 items-center justify-center gap-1.5 rounded-box px-3 font-semibold transition-[opacity,transform] active:enabled:scale-[.98] disabled:cursor-default disabled:opacity-40';
  const primary = `${btn} bg-blue text-t4 font-bold text-white active:enabled:bg-blue-dn`;
  const okOff = busy || (decision && !picked);
  const doOk = () => decide(decision || !hasNote ? 'ok' : 'answer');
  const ok = (
    <button type="button" disabled={okOff} aria-keyshortcuts={SEND_KEYS} onClick={doOk} className={primary}>
      <Icon name="check" className="size-[18px]" stroke={3} />
      {decision ? picked > 1 ? `${L.btn.decide} · ${picked}` : L.btn.decide : L.btn.done}
      <KbdHint />
    </button>
  );
  return (
    <>
      <textarea rows={2} value={note} disabled={busy} aria-label={L.label.note}
        placeholder={decision ? L.ph.answer : L.ph.review}
        onChange={e => setNote(e.target.value)}
        onKeyDown={e => { if (isSendKey(e)) { e.preventDefault(); if (!okOff) doOk(); } }}
        className="mb-2 block min-h-16 w-full resize-y rounded-box border-[1.5px] border-transparent bg-surface-3 px-4 py-3 text-base leading-normal outline-none focus:border-blue focus:bg-surface" />
      {decision ? (
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-2">
          <button type="button" disabled={busy || !hasNote} onClick={() => decide('answer')} className={`${btn} bg-grey-weak text-t5 text-ink-2`}>{L.btn.answer}</button>
          {ok}
        </div>
      ) : <div className="grid grid-cols-1 gap-2">{ok}</div>}
      <button type="button" disabled={busy} onClick={() => decide('hold')}
        className="mx-auto mt-1 flex h-11 items-center justify-center rounded-tile px-4 text-t5 font-semibold text-ink-3 disabled:opacity-40">{L.btn.hold}</button>
    </>
  );
}
