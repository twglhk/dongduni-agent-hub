import en from './en.json';
import ko from './ko.json';
import ja from './ja.json';
import zh from './zh.json';
import es from './es.json';
import langs from './langs.json';

export type Lang = 'en' | 'ko' | 'ja' | 'zh' | 'es';
export type Dict = typeof en;

const RAW: Record<Lang, object> = { en, ko, ja, zh, es };
export const LANGS = langs as Record<Lang, string>;

function merge<T extends object>(a: T, b: object): T {
  const o: any = { ...a };
  for (const [k, v] of Object.entries(b)) o[k] = v && typeof v === 'object' ? merge(o[k] || {}, v) : v;
  return o;
}

// Every language falls back to English for a key it lacks.
export function dictFor(code: Lang): Dict {
  return code === 'en' ? en : merge(en, RAW[code]);
}

export const isLang = (c: string): c is Lang => c in RAW;
const baseLang = (c?: string | null) => String(c || '').toLowerCase().split(/[-_]/)[0];

const KEY = 'tower.lang';
export function savedLang(): string { try { return localStorage.getItem(KEY) || ''; } catch { return ''; } }
export function saveLang(c: Lang): boolean { try { localStorage.setItem(KEY, c); return true; } catch { return false; } }

// This device's pick, then the workspace language, then the browser's.
export const pickLang = (server: string): Lang =>
  ([savedLang(), baseLang(server), baseLang(navigator.language)].find(isLang) as Lang | undefined) || 'en';
