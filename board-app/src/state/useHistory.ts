import { useCallback, useEffect, useRef, useState } from 'react';
import { get, HttpError } from '../lib/api';

interface Page<T> { kind: string; rows: T[]; more: boolean; loaded: boolean }

// One api/history kind, 20 rows a page. Rows carry the kind they were loaded for, so a switch never draws one kind's rows
// as another's.
export function useHistory<T>(kind: string, onError: (m: string) => void) {
  const [pg, setPg] = useState<Page<T>>({ kind, rows: [], more: false, loaded: false });
  const [busy, setBusy] = useState(false);
  const gen = useRef(0), count = useRef(0);

  const load = useCallback(async () => {
    const g = gen.current;
    setBusy(true);
    try {
      const j = await get<{ rows: T[]; more: boolean }>(`api/history?kind=${encodeURIComponent(kind)}&offset=${count.current}`);
      if (g !== gen.current) return;
      const got = j.rows || [];
      count.current += got.length;
      setPg(p => ({ kind, rows: p.kind === kind ? [...p.rows, ...got] : got, more: !!j.more, loaded: true }));
    } catch (e) {
      if (g === gen.current) {
        if ((e as HttpError).status !== 401) onError((e as Error).message);
        setPg(p => ({ ...p, kind, loaded: true }));
      }
    }
    if (g === gen.current) setBusy(false);
  }, [kind]);

  useEffect(() => {
    gen.current++; count.current = 0;
    setPg({ kind, rows: [], more: false, loaded: false });
    load();
  }, [kind]);

  const cur = pg.kind === kind;
  return { rows: cur ? pg.rows : [], more: cur && pg.more, busy, loaded: cur && pg.loaded, loadMore: () => { if (!busy) load(); } };
}
