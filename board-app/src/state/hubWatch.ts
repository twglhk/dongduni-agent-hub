import { useEffect, useRef } from 'react';
import type { BoardState } from '../lib/types';
import type { Route } from './route';
import { go } from './route';

const waiting = (s?: string) => s === 'starting' || s === 'full';

export function useHubWatch(data: BoardState | null, r: Route, onReady: () => void) {
  const was = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!data) return;
    const now = data.my_hub_state?.state || 'off';
    const before = was.current;
    was.current = now;
    if (before === undefined) {
      if (waiting(now) && r.view !== 'hub' && r.item === null && !r.lane) go('hub');
      return;
    }
    if (now === 'ready' && waiting(before)) {
      if (r.view !== 'hub') go('hub');
      onReady();
    }
  }, [data, r, onReady]);
}
