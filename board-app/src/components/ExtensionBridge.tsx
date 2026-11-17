import { useEffect } from 'react';
import { bridge, xLoaded, xStart } from '../ext/registry';
import { useL } from '../i18n/context';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';

export function ExtensionBridge() {
  const { L, lang } = useL();
  const { data, loads, refresh } = useBoard();
  const toast = useToast();
  bridge.data = data; bridge.lang = lang; bridge.L = L; bridge.toast = toast; bridge.refresh = () => { refresh(); };
  useEffect(() => {
    if (!data) return;
    xStart(data.extensions);
    xLoaded();
  }, [loads]);
  return null;
}
