import { useEffect } from 'react';
import { laneSheet } from '../components/lane/LaneSheet';
import type { SheetContent } from '../components/Sheet';
import { useL } from '../i18n/context';
import { laneLabel } from '../lib/items';
import type { LaneDetail } from '../lib/laneTypes';
import { useBoard } from './board';
import { closeItem } from './route';
import { useToast } from './toast';
import { usePoll } from './usePoll';

const LANE_MS = 10000;

export function useLaneSheet(id: string | null): SheetContent | null {
  const { L } = useL();
  const { data } = useBoard();
  const toast = useToast();
  const detail = usePoll<LaneDetail>(id ? 'api/lanes/' + encodeURIComponent(id) : null, LANE_MS);
  useEffect(() => { if (id && detail.missing) { toast(L.msg.notFound, true); closeItem(); } }, [id, detail.missing]);
  if (!id) return null;
  return laneSheet({ id, label: laneLabel(data, id), d: detail.data });
}
