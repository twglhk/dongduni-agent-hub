import { useEffect, type ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { useBoard } from '../../state/board';
import { act, loadAccount, setCode, setConfirm, useAccount, type AcAct } from '../../state/claude';
import { useToast } from '../../state/toast';
import { Icon } from '../Icon';
import { SkelLines } from '../Skeleton';

type LL = ReturnType<typeof useL>['L'];
const BIG = 'flex h-14 w-full items-center justify-center rounded-box text-t4 font-bold active:scale-[.98] disabled:opacity-40';
const PRIMARY = `${BIG} bg-blue text-white`;
const WEAK = 'flex h-12 w-full items-center justify-center rounded-box text-t5 font-semibold text-ink-2 active:bg-[var(--press)] disabled:opacity-40';

function Dots() {
  return <span className="inline-flex gap-1" aria-hidden="true">{[0, 150, 300].map(ms => <i key={ms} className="size-1.5 rounded-full bg-current animate-[beat_1.2s_ease-in-out_infinite]" style={{ animationDelay: ms + 'ms' }} />)}</span>;
}

function Note({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-box bg-surface-3 px-5 py-5">
      <div className="text-t4 font-bold leading-snug">{title}</div>
      {children && <div className="mt-1 text-t5 leading-normal text-ink-2">{children}</div>}
    </div>
  );
}

function Kv({ k, v }: { k: string; v?: string }) {
  if (!v) return null;
  return (
    <div className="flex min-h-[52px] items-center gap-3 px-1">
      <span className="flex-none text-t5 text-ink-3">{k}</span>
      <span className="min-w-0 flex-1 truncate text-right text-t5 font-semibold">{v}</span>
    </div>
  );
}

const plan = (p?: string) => p ? p.charAt(0).toUpperCase() + p.slice(1) : undefined;
function until(L: LL, lang: string, iso?: string) {
  const t = iso ? Date.parse(iso) : NaN;
  return isNaN(t) ? undefined : L.ca.until.replace('{d}', new Date(t).toLocaleDateString(lang, { month: 'long', day: 'numeric' }));
}

function useRun() {
  const { L } = useL();
  const toast = useToast();
  return async (a: AcAct) => {
    const why = await act(a);
    if (why === 'signin' || why === 'busy') return;
    if (why) { toast((L.ca.err as Record<string, string>)[why] || L.ca.err.failed, true); return; }
    if (a !== 'start') toast(a === 'code' ? L.ca.doneOn : a === 'cancel' ? L.ca.doneStop : L.ca.doneOff);
  };
}

function OwnAccount() {
  const { L, lang } = useL();
  const ac = useAccount();
  const run = useRun();
  useEffect(() => { setConfirm(false); loadAccount(); }, []);

  if (ac.state === 'loading') return <div className="pt-2"><SkelLines widths={[80, 55, 65]} /></div>;
  if (ac.state === 'nomember') return <Note title={L.ca.noMember}>{L.ca.noMemberSub}</Note>;
  if (ac.state === 'off') return <Note title={L.ca.disabled}>{L.ca.disabledSub}</Note>;
  if (ac.state === 'error') return <Note title={L.ca.error}>{L.ca.errorSub}</Note>;
  if (ac.state === 'none') return (
    <>
      {ac.ended ? <Note title={L.ca.endedTitle}>{L.ca.endedSub}</Note> : <Note title={L.ca.noneTitle}>{L.ca.noneSub}</Note>}
      <p className="mt-4 px-1 text-t6 leading-normal text-ink-3">{L.ca.risk}</p>
    </>
  );
  if (ac.state === 'pending') return (
    <>
      <ol className="grid gap-5">
        <li>
          <div className="mb-2 px-1 text-t5 font-semibold">{L.ca.step1}</div>
          {ac.url
            ? <a href={ac.url} target="_blank" rel="noopener noreferrer" className="flex h-14 w-full items-center justify-center gap-1.5 rounded-box bg-blue-weak text-t5 font-semibold text-blue-ink active:scale-[.98]">{L.ca.open}<Icon name="next" className="size-[18px]" /></a>
            : <SkelLines widths={[100]} />}
        </li>
        <li>
          <label htmlFor="ca-code" className="mb-2 block px-1 text-t5 font-semibold">{L.ca.step2}</label>
          <input id="ca-code" type="text" value={ac.code} onChange={e => setCode(e.target.value)} placeholder={L.ca.ph}
            autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} disabled={ac.busy === 'code'}
            onKeyDown={e => { if (e.key === 'Enter' && ac.code.trim()) run('code'); }}
            className="h-14 w-full rounded-box bg-surface-3 px-4 font-mono text-t4 outline-none placeholder:font-sans placeholder:text-ink-4 focus:shadow-[inset_0_0_0_2px_var(--blue)]" />
        </li>
      </ol>
      <p className="mt-4 px-1 text-t6 leading-normal text-ink-3">{L.ca.wait}</p>
      <button type="button" disabled={!!ac.busy} onClick={() => run('cancel')} className={`mt-2 ${WEAK}`}>{L.ca.stop}</button>
    </>
  );
  return (
    <>
      <div className="flex items-center gap-2 px-1 pb-1">
        <span className={`size-2 flex-none rounded-full ${ac.state === 'connected' ? 'bg-green' : 'bg-orange'}`} />
        <span className="text-t4 font-bold">{ac.state === 'connected' ? L.ca.st.connected : L.ca.st.expired}</span>
      </div>
      <div className="divide-y divide-line">
        <Kv k={L.ca.email} v={ac.email} />
        <Kv k={L.ca.plan} v={plan(ac.plan)} />
        <Kv k={L.ca.valid} v={until(L, lang, ac.expires)} />
      </div>
      <p className="mt-3 px-1 text-t6 leading-normal text-ink-3">{ac.state === 'connected' ? L.ca.shared : L.ca.expiredSub}</p>
      {ac.confirm ? (
        <div className="mt-5 rounded-box bg-red-weak px-5 py-4">
          <div className="text-t5 font-semibold text-red-ink">{L.ca.offAsk}</div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setConfirm(false)} className="h-12 rounded-box bg-surface text-t5 font-semibold text-ink-2">{L.ca.later}</button>
            <button type="button" disabled={!!ac.busy} onClick={() => run('disconnect')} className="h-12 rounded-box bg-red text-t5 font-bold text-white disabled:opacity-40">{ac.busy === 'disconnect' ? <Dots /> : L.ca.offYes}</button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setConfirm(true)} className={`mt-4 ${WEAK} text-red-ink`}>{L.ca.off}</button>
      )}
    </>
  );
}

const TONE = { connected: 'bg-green', ended: 'bg-orange', none: 'bg-ink-4' } as const;

function Team() {
  const { L } = useL();
  const { data } = useBoard();
  const rows = data?.accounts;
  if (!rows || !rows.length) return null;
  return (
    <section className="mt-8">
      <h3 className="mb-1 px-1 text-t6 font-semibold text-ink-3">{L.ca.team}</h3>
      <div className="divide-y divide-line">
        {rows.map(r => (
          <div key={r.member} className="flex min-h-[48px] items-center gap-3 px-1">
            <i className={`size-2 flex-none rounded-full ${TONE[r.state]}`} />
            <span className="min-w-0 flex-1 truncate text-t5">{r.member}</span>
            <span className="flex-none text-t6 text-ink-3">{L.ca.st[r.state === 'ended' ? 'expired' : r.state]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ClaudeBody() {
  return <><OwnAccount /><Team /></>;
}

export function ClaudeCta({ close }: { close: () => void }) {
  const { L } = useL();
  const ac = useAccount();
  const run = useRun();
  if (ac.state === 'none' || ac.state === 'expired') return (
    <button type="button" disabled={!!ac.busy} onClick={() => run('start')} className={PRIMARY}>
      {ac.busy === 'start' ? <Dots /> : ac.state === 'none' ? L.ca.start : L.ca.again}
    </button>
  );
  if (ac.state === 'pending') return (
    <button type="button" disabled={!!ac.busy || !ac.code.trim()} onClick={() => run('code')} className={PRIMARY}>
      {ac.busy === 'code' ? <Dots /> : L.ca.send}
    </button>
  );
  return <button type="button" onClick={close} className={`${BIG} bg-grey-weak text-t5 font-semibold text-ink-2`}>{L.btn.close}</button>;
}

export function ClaudeRow({ onOpen }: { onOpen: () => void }) {
  const { L } = useL();
  const ac = useAccount();
  useEffect(() => { loadAccount(); }, []);
  const s = ac.state === 'connected' || ac.state === 'expired' || ac.state === 'pending' || ac.state === 'none' ? L.ca.st[ac.state] : '';
  return (
    <button type="button" onClick={onOpen}
      className="mb-2 flex min-h-[60px] w-full items-center gap-3 rounded-box bg-surface-3 px-4 py-3 text-left active:bg-[var(--press)]">
      <span className="min-w-0 flex-1">
        <span className="block text-t5 font-semibold leading-snug">{L.ca.title}</span>
        {ac.email && <span className="mt-0.5 block truncate text-t6 text-ink-3">{ac.email}</span>}
      </span>
      <span className={`flex-none text-t6 font-semibold ${ac.state === 'connected' ? 'text-green-ink' : ac.state === 'expired' ? 'text-orange-ink' : 'text-ink-3'}`}>{s}</span>
      <Icon name="chev" className="size-5 text-ink-4" />
    </button>
  );
}
