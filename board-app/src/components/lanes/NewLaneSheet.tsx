import { useEffect, useRef } from 'react';
import { useL } from '../../i18n/context';
import { useBoard } from '../../state/board';
import { sendNewLane, setNewLaneText, useNewLane } from '../../state/newLane';
import { useToast } from '../../state/toast';

export function NewLaneBody() {
  const { L } = useL();
  const nl = useNewLane();
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { const t = setTimeout(() => box.current?.focus({ preventScroll: true }), 350); return () => clearTimeout(t); }, []);
  return (
    <div className="grid gap-2">
      <label htmlFor="nl-text" className="px-1 text-t5 font-semibold">{L.nl.label}</label>
      <textarea id="nl-text" ref={box} rows={4} maxLength={4000} placeholder={L.nl.ph} value={nl.text} disabled={nl.busy}
        onChange={e => setNewLaneText(e.target.value)}
        className="min-h-28 w-full resize-y rounded-box border-[1.5px] border-transparent bg-surface-3 px-4 py-3 text-base leading-normal outline-none focus:border-blue focus:bg-surface" />
      <p className="px-1 text-t6 leading-normal text-ink-3">{L.nl.hint}</p>
    </div>
  );
}

export function NewLaneCta({ close }: { close: () => void }) {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const nl = useNewLane();
  const send = async () => {
    const r = await sendNewLane();
    if (r === 'sent') { close(); toast(L.nl.sent); refresh(); }
    else if (r === 'failed') toast(L.msg.fail, true);
  };
  return (
    <button type="button" disabled={nl.busy} onClick={send}
      className="flex h-14 w-full items-center justify-center rounded-box bg-blue text-t4 font-bold text-white active:scale-[.98] disabled:opacity-40">
      {nl.busy ? '…' : L.nl.send}
    </button>
  );
}
