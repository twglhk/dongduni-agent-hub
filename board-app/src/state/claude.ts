import { useEffect, useSyncExternalStore } from 'react';
import { get, HttpError, post } from '../lib/api';

export type AcState = 'loading' | 'none' | 'pending' | 'connected' | 'expired' | 'nomember' | 'off' | 'error';
export type AcAct = 'start' | 'code' | 'cancel' | 'disconnect';
export interface Ac { state: AcState; url?: string; email?: string; plan?: string; expires?: string; ended?: boolean; busy: AcAct | null; code: string; confirm: boolean }
interface Answer { state: AcState; url?: string; email?: string; plan?: string; expires?: string; ended?: boolean }

let st: Ac = { state: 'loading', busy: null, code: '', confirm: false };
const subs = new Set<() => void>();
const set = (x: Partial<Ac>) => { st = { ...st, ...x }; subs.forEach(f => f()); };
const take = (a: Answer) => set({ state: a.state, url: a.url, email: a.email, plan: a.plan, expires: a.expires, ended: !!a.ended });

export const useAccount = () => useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => st);
export const setCode = (code: string) => set({ code });
export const setConfirm = (confirm: boolean) => set({ confirm });

const OPEN = 'tower:account';
export const openAccount = () => window.dispatchEvent(new Event(OPEN));
export function useOpenAccount(fn: () => void) {
  useEffect(() => { window.addEventListener(OPEN, fn); return () => window.removeEventListener(OPEN, fn); }, [fn]);
}

export async function loadAccount() {
  if (st.busy) return;
  try { take(await get<Answer>('api/me/claude')); }
  catch (e) { set({ state: (e as HttpError).status === 403 ? 'nomember' : (e as HttpError).status === 404 ? 'off' : 'error' }); }
}

export async function act(a: AcAct): Promise<string> {
  if (st.busy) return 'busy';
  set({ busy: a });
  try {
    const out = await post<Answer>('api/me/claude', a === 'code' ? { action: a, code: st.code.trim() } : { action: a });
    take(out);
    set({ busy: null, code: a === 'code' ? '' : st.code, confirm: false });
    return '';
  } catch (e) {
    set({ busy: null });
    if ((e as HttpError).status === 401) return 'signin';
    if (a !== 'code' || (e as Error).message !== 'bad_code') await loadAccount();
    return (e as Error).message || 'failed';
  }
}
