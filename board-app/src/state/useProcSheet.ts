import { useEffect } from 'react';
import { fullSheet, loadingSheet, subSheet } from '../components/procs/ProcSheets';
import type { SheetContent } from '../components/Sheet';
import { useL } from '../i18n/context';
import { laneLabel } from '../lib/items';
import { useBoard } from './board';
import { closeItem } from './route';
import { useToast } from './toast';
import { useProcesses } from './useProcesses';

export function useProcSheet(full: string | null, sub: string | null): SheetContent | null {
  const { L } = useL();
  const { data } = useBoard();
  const toast = useToast();
  const lib = useProcesses(!!(full || sub));
  const v = lib.data;
  const f = v && full ? v.full.find(x => x.id === full) : undefined;
  const s = v && sub ? v.sub.find(x => x.id === sub) : undefined;
  const gone = !!v && ((!!full && !f) || (!!sub && !s));
  useEffect(() => { if (gone) { toast(L.msg.notFound, true); closeItem(); } }, [gone]);
  if (!full && !sub) return null;
  if (!v) return loadingSheet(full ? 'full-' + full : 'sub-' + sub);
  const lane = (id: string) => laneLabel(data, id);
  if (f) return fullSheet(f, v, lane);
  if (s) return subSheet(s, v, lane);
  return null;
}
