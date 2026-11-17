import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useL } from '../i18n/context';
import { get, HttpError, post } from '../lib/api';
import { applyLook, cache, cached, DEFAULT_MODE, effective, merge, type Change, type Prefs, type Saved, type SecTab } from '../lib/prefs';
import { setFirstTab } from './route';
import { useToast } from './toast';

const POLL_MS = 30000;

interface PrefsCtx {
  p: Prefs;
  saved: Saved;
  ok: boolean | null;
  set: (c: Change) => void;
  reset: () => void;
}

const Ctx = createContext<PrefsCtx | null>(null);
export const usePrefs = () => useContext(Ctx)!;

const OPEN = 'tower-open-prefs';
export const openPrefs = () => window.dispatchEvent(new Event(OPEN));
export function useOpenPrefs(fn: () => void) {
  useEffect(() => { window.addEventListener(OPEN, fn); return () => window.removeEventListener(OPEN, fn); }, [fn]);
}

const start = cached();
applyLook(effective(start));
setFirstTab(effective(start).first_tab);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const { L } = useL();
  const toast = useToast();
  const [saved, setSaved] = useState<Saved>(start);
  const [ok, setOk] = useState<boolean | null>(null);
  const seq = useRef(0), pending = useRef(0), first = useRef(true), now = useRef<Saved>(start);
  const keep = (s: Saved) => { now.current = s; setSaved(s); cache(s); };

  const take = useCallback((s: Saved) => {
    keep(s);
    if (first.current) {
      first.current = false;
      const t = effective(s).first_tab;
      setFirstTab(t);
      if (!DEFAULT_MODE && !location.hash && t !== 'hub') location.replace(location.pathname + location.search + '#' + t);
    }
  }, []);

  const load = useCallback(async () => {
    if (pending.current) return;
    const n = seq.current;
    try {
      const j = await get<{ saved: Saved }>('api/me/prefs');
      setOk(true);
      if (n === seq.current && !pending.current) take(j.saved || {});
    } catch (e) {
      const st = (e as HttpError).status;
      if (st === 403 || st === 404) setOk(false);
    }
  }, [take]);

  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', vis); };
  }, [load]);

  const send = useCallback(async (body: object, next: Saved) => {
    const n = ++seq.current;
    pending.current++;
    keep(next);
    try {
      const j = await post<{ saved: Saved }>('api/me/prefs', body);
      if (n === seq.current) take(j.saved || {});
    } catch (e) {
      if ((e as HttpError).status !== 401) toast((e as Error).message || L.msg.fail, true);
      pending.current--;
      return load();
    }
    pending.current--;
  }, [take, load, toast, L]);

  const set = useCallback((c: Change) => { send({ prefs: c }, merge(now.current, c)); }, [send]);
  const reset = useCallback(() => { const seen = now.current.seen; send({ reset: true }, seen?.length ? { seen } : {}); toast(L.pf.resetDone); }, [send, toast, L]);

  const p = effective(DEFAULT_MODE ? {} : saved);
  useEffect(() => { applyLook(p); setFirstTab(p.first_tab); }, [p.font, p.accent, p.first_tab]);

  return <Ctx.Provider value={{ p, saved, ok, set, reset }}>{children}</Ctx.Provider>;
}

export function useFold() {
  const { p, ok, set } = usePrefs();
  return (tab: SecTab, id: string) => {
    if (ok === false) return undefined;
    const c = p.sections[tab].collapsed, folded = c.includes(id);
    return { folded, toggle: () => set({ sections: { [tab]: { collapsed: folded ? c.filter(x => x !== id) : [...c, id] } } }) };
  };
}
