import { useState } from 'react';
import { useL } from '../i18n/context';
import { HttpError, post } from '../lib/api';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';

export function ReachCard() {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    if (busy) return;
    setBusy(true);
    try { await post('api/memo', { to: 'hub', text: L.reach.memo }); toast(L.msg.sent); }
    catch (e) { if ((e as HttpError).status !== 401) toast((e as Error).message, true); }
    setBusy(false); refresh();
  };
  return (
    <section className="mb-8">
      <div className="grid gap-2 rounded-card bg-orange-weak px-5 py-4">
        <div className="text-t5 font-bold text-orange-ink">{L.reach.title}</div>
        <div className="text-t5 leading-relaxed text-ink-2">{L.reach.body}</div>
        <div className="flex justify-end">
          <button type="button" disabled={busy} onClick={ask} className="h-11 rounded-tile bg-blue px-5 text-t5 font-bold text-white disabled:opacity-35">{L.reach.ask}</button>
        </div>
      </div>
    </section>
  );
}
