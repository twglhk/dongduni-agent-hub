import { useSyncExternalStore } from 'react';
import { HttpError, post } from '../lib/api';

// The hub finds the request by this first line (templates/lanes/hub.md); it is a protocol word, not board text.
export const NEW_LANE_MARK = '[New lane request]';

interface Nl { text: string; busy: boolean }
let st: Nl = { text: '', busy: false };
const subs = new Set<() => void>();
const set = (x: Partial<Nl>) => { st = { ...st, ...x }; subs.forEach(f => f()); };

export const useNewLane = () => useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => st);
export const setNewLaneText = (text: string) => set({ text });

export async function sendNewLane(): Promise<'sent' | 'failed' | 'auth' | 'busy'> {
  if (st.busy) return 'busy';
  const body = st.text.trim();
  set({ busy: true });
  try {
    await post('api/memo', { to: 'hub', text: body ? `${NEW_LANE_MARK}\n${body}` : NEW_LANE_MARK });
    set({ text: '', busy: false });
    return 'sent';
  } catch (e) {
    set({ busy: false });
    return (e as HttpError).status === 401 ? 'auth' : 'failed';
  }
}
