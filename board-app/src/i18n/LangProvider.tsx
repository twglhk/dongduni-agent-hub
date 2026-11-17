import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { configureApi } from '../lib/api';
import { useBoard } from '../state/board';
import { LangContext } from './context';
import { dictFor, pickLang, saveLang, type Lang } from './dict';

export function LangProvider({ children }: { children: ReactNode }) {
  const { data, lock } = useBoard();
  const [picked, setPicked] = useState<Lang | null>(null);
  const lang = picked || pickLang(data?.lang || '');
  const L = useMemo(() => dictFor(lang), [lang]);
  const setLang = useCallback((c: Lang) => { setPicked(c); return saveLang(c); }, []);
  configureApi({ net: L.msg.net, fail: L.msg.fail, badToken: L.msg.badToken }, lock);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  return <LangContext.Provider value={{ lang, L, setLang }}>{children}</LangContext.Provider>;
}
