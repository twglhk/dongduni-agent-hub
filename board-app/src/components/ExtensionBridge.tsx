import { useEffect } from 'react';
import { bridge, xLoaded, xStart } from '../ext/registry';
import { useL } from '../i18n/context';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';

// Lends window.dah the board's current state, language, toast and refresh, runs onState callbacks after each state load,
// and loads the workspace's extra.css / extra.js once the first state is in.
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
