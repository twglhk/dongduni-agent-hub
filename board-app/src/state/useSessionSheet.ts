import { useEffect } from 'react';
import { sessionSheet } from '../components/lanes/SessionSheet';
import type { SheetContent } from '../components/Sheet';
import { useL } from '../i18n/context';
import { laneLabel } from '../lib/items';
import type { SessionView } from '../lib/laneTypes';
import { useBoard } from './board';
import { closeItem } from './route';
import { useToast } from './toast';
import { usePoll } from './usePoll';

const SESSION_MS = 8000;

export function useSessionSheet(key: string | null): SheetContent | null {
  const { L } = useL();
  const { data } = useBoard();
  const toast = useToast();
  const view = usePoll<SessionView>(key ? 'api/sessions/' + encodeURIComponent(key) : null, SESSION_MS);
  useEffect(() => { if (key && view.missing) { toast(L.msg.notFound, true); closeItem(); } }, [key, view.missing]);
  useEffect(() => { if (key && view.failed && !view.data) toast(L.msg.loadFail, true); }, [key, view.failed]);
  if (!key) return null;
  const base = key.split('#')[0];
  const n = key.includes('#') ? key.slice(key.indexOf('#') + 1) : '';
  return sessionSheet({ key, label: laneLabel(data, base) + (n ? ' #' + n : ''), v: view.data, status: data?.lanes.find(l => l.lane === key)?.status });
}
