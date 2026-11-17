import { useRef, useState } from 'react';
import { useL } from '../../i18n/context';
import { HttpError, post } from '../../lib/api';
import { laneMemo, LANE_ID } from '../../lib/laneMemo';
import { RUNTIMES, rtName } from '../../lib/machine';
import type { BoardState } from '../../lib/types';
import { useBoard } from '../../state/board';
import { useToast } from '../../state/toast';
import { Icon } from '../Icon';

const FIELD = 'w-full rounded-box border-[1.5px] border-transparent bg-surface-3 px-4 text-base outline-none focus:border-blue focus:bg-surface aria-[invalid=true]:border-red';

function Fr({ label, id, children }: { label: string; id?: string; children: React.ReactNode }) {
  return <div className="grid min-w-0 content-start gap-1.5"><label htmlFor={id} className="px-1 text-t6 font-semibold text-ink-2">{label}</label>{children}</div>;
}

export function LaneAdd({ d }: { d: BoardState }) {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const rts = d.runtimes?.length ? d.runtimes : Object.keys(RUNTIMES);
  const def = rts.includes(d.default_runtime || '') ? d.default_runtime! : rts[0];
  const empty = { id: '', label: '', model: '', agent: '', role: '' };
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(empty);
  const [rt, setRt] = useState(def);
  const [busy, setBusy] = useState(false);
  const tog = useRef<HTMLButtonElement>(null), idRef = useRef<HTMLInputElement>(null);
  const id = f.id.trim();
  const err = !id ? '' : !LANE_ID.test(id) ? L.lane.idBad : d.lanes.some(l => l.lane.toLowerCase() === id.toLowerCase()) ? L.lane.idDup : '';
  const can = !busy && !!id && !err && !!f.role.trim();
  const set = (k: keyof typeof empty) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  const input = (k: 'id' | 'label' | 'model' | 'agent', max: number, ph: string, extra = {}) => (
    <input id={'la-' + k} type="text" autoComplete="off" spellCheck={false} maxLength={max} placeholder={ph}
      value={f[k]} onChange={set(k)} className={`${FIELD} h-14`} {...extra} />
  );
  const send = async () => {
    if (!can) return;
    const text = laneMemo(L.lane.memo, { id, runtime: rt, label: f.label.trim(), model: f.model.trim(), agent: rt === 'codex' ? '' : f.agent.trim(), role: f.role.trim() });
    setBusy(true);
    try {
      await post('api/memo', { to: 'hub', text });
      setF(empty); setRt(def); setOpen(false); tog.current?.focus(); toast(L.lane.sent); refresh();
    } catch (e) { if ((e as HttpError).status !== 401) toast((e as Error).message, true); }
    setBusy(false);
  };
  return (
    <div className="mt-6 overflow-hidden rounded-card bg-surface">
      <button ref={tog} type="button" aria-expanded={open} aria-controls="la-body"
        onClick={() => { setOpen(!open); if (!open) requestAnimationFrame(() => idRef.current?.focus()); }}
        className="flex min-h-14 w-full items-center gap-2 px-5 text-left text-t5 font-semibold text-blue-ink">
        <Icon name="plus" /><span className="flex-1">{L.lane.add}</span>
        <Icon name="chev" className={`size-5 text-ink-4 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div id="la-body" className="relative grid gap-4 border-t border-line mx-5 py-4">
          <div className="grid grid-cols-1 gap-4 min-[600px]:grid-cols-2">
            <Fr label={L.lane.id} id="la-id">
              {input('id', 32, L.lane.idPh, { ref: idRef, autoCapitalize: 'off', required: true, 'aria-describedby': 'la-idm', 'aria-invalid': err ? 'true' : undefined })}
              <div id="la-idm" aria-live="polite" className={`px-1 text-t6 leading-[1.45] ${err ? 'text-red-ink' : 'text-ink-3'}`}>{err || L.lane.idHelp}</div>
            </Fr>
            <Fr label={L.lane.label} id="la-label">{input('label', 60, L.lane.labelPh)}</Fr>
          </div>
          <div className="grid gap-1.5">
            <span id="la-rt" className="px-1 text-t6 font-semibold text-ink-2">{L.lane.runtime}</span>
            <div role="group" aria-labelledby="la-rt" className="no-scrollbar flex gap-0.5 overflow-x-auto rounded-tile bg-grey-weak p-[3px]">
              {rts.map(r => (
                <button key={r} type="button" aria-pressed={rt === r} onClick={() => setRt(r)}
                  className={`h-[38px] min-h-[38px] flex-[1_0_auto] whitespace-nowrap rounded-[9px] px-2 text-t6 font-semibold ${rt === r ? 'bg-surface text-ink shadow-[0_1px_3px_rgba(2,32,71,.10)]' : 'text-ink-3'}`}>{rtName(r)}</button>
              ))}
            </div>
          </div>
          <Fr label={L.lane.role} id="la-role">
            <textarea id="la-role" rows={4} maxLength={4000} placeholder={L.lane.rolePh} required value={f.role} onChange={set('role')}
              className={`${FIELD} min-h-28 resize-y py-3 leading-normal`} />
          </Fr>
          <div className="grid grid-cols-1 gap-4 min-[600px]:grid-cols-2">
            <Fr label={L.lane.model} id="la-model">{input('model', 120, L.lane.optPh, { autoCapitalize: 'off' })}</Fr>
            {rt !== 'codex' && <Fr label={L.lane.agent} id="la-agent">{input('agent', 80, L.lane.optPh, { autoCapitalize: 'off' })}</Fr>}
          </div>
          <div className="flex justify-end">
            <button type="button" disabled={!can} onClick={send}
              className="h-11 rounded-tile bg-blue px-5 text-t5 font-bold text-white transition disabled:cursor-default disabled:opacity-35 active:not-disabled:scale-97">{L.lane.send}</button>
          </div>
          <div className="px-1 text-t6 leading-normal text-ink-3">{L.lane.hint}</div>
        </div>
      )}
    </div>
  );
}
