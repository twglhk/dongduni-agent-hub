import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { get, HttpError } from '../lib/api';
import type { MachineInfo } from '../lib/machine';
import type { BoardState, Item, Workspace } from '../lib/types';

const POLL_MS = 5000;
const WS_MS = 30000;
const MACH_MS = 10000;

interface Board {
  data: BoardState | null;
  loads: number;  // successful state loads so far, changed or not
  ok: boolean | null;
  locked: boolean;
  ws: Workspace[];
  mach: MachineInfo | null;
  refresh: () => Promise<void>;
  loadWs: () => Promise<void>;
  unlock: () => void;
  lock: () => void;
  itemById: (id: number) => Item | undefined;
  fetchItem: (id: number) => Promise<Item | null>;
}

const BoardContext = createContext<Board | null>(null);
export const useBoard = () => useContext(BoardContext)!;

function normalize(j: any): BoardState {
  for (const k of ['items', 'tasks', 'reports', 'lanes']) if (!Array.isArray(j[k])) j[k] = [];
  j.memos = { hub: [], ...(j.memos || {}) };
  j.counts = j.counts || {};
  return j;
}

export function BoardProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<BoardState | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  const [locked, setLocked] = useState(false);
  const [ws, setWs] = useState<Workspace[]>([]);
  const [loads, setLoads] = useState(0);
  const [mach, setMach] = useState<MachineInfo | null>(null);
  const [extra, setExtra] = useState<Record<number, Item>>({});
  const raw = useRef(''), busy = useRef(false), again = useRef(false), lockedRef = useRef(false);

  const lock = useCallback(() => { lockedRef.current = true; setLocked(true); }, []);

  const load = useCallback(async () => {
    if (lockedRef.current) return;
    if (busy.current) { again.current = true; return; }
    busy.current = true; again.current = false;
    try {
      const j = normalize(await get('api/state'));
      setOk(true);
      setLoads(n => n + 1);
      const s = JSON.stringify(j);
      if (s !== raw.current) { raw.current = s; setData(j); }
    } catch (e) { if ((e as HttpError).status !== 401) setOk(false); }
    busy.current = false;
    if (again.current) load();
  }, []);

  const rawMach = useRef('');
  const loadMach = useCallback(async () => {
    if (lockedRef.current) return;
    try {
      const j = await get<MachineInfo>('api/machine'), t = JSON.stringify(j);
      if (t !== rawMach.current) { rawMach.current = t; setMach(j); }
    } catch { /* the footer and the Info tab just keep the last reading */ }
  }, []);

  const refresh = useCallback(() => { raw.current = ''; return load(); }, [load]);

  const loadWs = useCallback(async () => {
    if (lockedRef.current) return;
    try { setWs((await get<{ workspaces: Workspace[] }>('api/workspaces'))?.workspaces || []); }
    catch (e) { if ((e as HttpError).status === 404) setWs([]); }
  }, []);

  const unlock = useCallback(() => { lockedRef.current = false; setLocked(false); refresh().then(loadWs); loadMach(); }, [refresh, loadWs, loadMach]);

  useEffect(() => {
    load().then(() => { loadWs(); loadMach(); });
    const a = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    const b = setInterval(() => { if (!document.hidden) loadWs(); }, WS_MS);
    const c = setInterval(() => { if (!document.hidden) loadMach(); }, MACH_MS);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearInterval(a); clearInterval(b); clearInterval(c); document.removeEventListener('visibilitychange', vis); };
  }, [load, loadWs, loadMach]);

  const itemById = useCallback((id: number) => data?.items.find(x => x.id === id) || extra[id], [data, extra]);
  const fetchItem = useCallback(async (id: number) => {
    const it = await get<Item>('api/items/' + id);
    setExtra(x => ({ ...x, [id]: it }));
    return it;
  }, []);

  return (
    <BoardContext.Provider value={{ data, loads, ok, locked, ws, mach, refresh, loadWs, unlock, lock, itemById, fetchItem }}>
      {children}
    </BoardContext.Provider>
  );
}
