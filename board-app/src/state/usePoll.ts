import { useCallback, useEffect, useRef, useState } from 'react';
import { get, HttpError } from '../lib/api';

export interface Polled<T> { data: T | null; missing: boolean; reload: () => void }

export function usePoll<T>(path: string | null, ms: number): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [missing, setMissing] = useState(false);
  const raw = useRef('');
  const load = useCallback(async () => {
    if (!path || document.hidden) return;
    try {
      const j = await get<T>(path), s = JSON.stringify(j);
      setMissing(false);
      if (s !== raw.current) { raw.current = s; setData(j); }
    } catch (e) { if ((e as HttpError).status === 404) setMissing(true); }
  }, [path]);
  useEffect(() => {
    raw.current = ''; setData(null);
    load();
    const t = setInterval(load, ms);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', vis); };
  }, [load, ms]);
  return { data, missing, reload: load };
}
