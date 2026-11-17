import { createContext, useContext } from 'react';
import { dictFor, type Dict, type Lang } from './dict';

interface LangValue { lang: Lang; L: Dict; setLang: (c: Lang) => boolean }
export const LangContext = createContext<LangValue>({ lang: 'en', L: dictFor('en'), setLang: () => false });
export const useL = () => useContext(LangContext);
