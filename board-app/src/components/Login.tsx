import { useEffect, useRef, useState } from 'react';
import { useL } from '../i18n/context';
import { HttpError, post } from '../lib/api';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';
import { Icon } from './Icon';

export function Login() {
  const { L } = useL();
  const { unlock } = useBoard();
  const toast = useToast();
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const inp = useRef<HTMLInputElement>(null);
  useEffect(() => {
    document.body.classList.add('locked');
    const t = setTimeout(() => inp.current?.focus(), 50);
    return () => { clearTimeout(t); document.body.classList.remove('locked'); };
  }, []);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim() || busy) return;
    setBusy(true);
    try { await post('api/login', { token: token.trim() }); unlock(); }
    catch (err) { toast((err as HttpError).status === 401 ? L.msg.badToken : (err as Error).message, true); inp.current?.select(); }
    setBusy(false);
  };
  const field = 'h-14 w-full rounded-box border-[1.5px] border-transparent bg-surface-3 px-4 text-base outline-none focus:border-blue focus:bg-surface';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg p-5">
      <form onSubmit={submit} className="grid w-full max-w-[360px] gap-3 rounded-card bg-surface px-6 pt-8 pb-6">
        <div className="flex size-12 items-center justify-center rounded-[14px] bg-blue-weak text-blue"><Icon name="lock" className="size-6" stroke={2} /></div>
        <h1 className="mt-2 mb-3 text-t2 font-bold tracking-[-.01em]">{L.login.title}</h1>
        <input ref={inp} type="password" value={token} onChange={e => setToken(e.target.value)} placeholder={L.ph.token} aria-label={L.ph.token}
          autoComplete="current-password" autoCapitalize="off" spellCheck={false} className={field} />
        <button type="submit" disabled={busy} className="flex h-14 items-center justify-center rounded-box bg-blue text-t4 font-bold text-white disabled:opacity-40">{L.btn.login}</button>
      </form>
    </div>
  );
}
