import { useState } from 'react';
import { useL } from '../i18n/context';
import { HttpError, post } from '../lib/api';
import { useBoard } from './board';
import { useToast } from './toast';

export function useCap() {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const setCap = async (n: number) => {
    if (busy) return;
    setBusy(true);
    try { await post('api/settings', { max_lanes: n }); toast(L.msg.saved); }
    catch (e) { if ((e as HttpError).status !== 401) toast((e as Error).message, true); }
    setBusy(false);
    refresh();
  };
  return { busy, setCap };
}
