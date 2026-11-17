import type { Tone } from '../components/Chips';
import type { Dict } from '../i18n/dict';
import type { FullProcess, ProcLib } from './procTypes';

export const procDocUrl = (path: string) => `api/processes/doc?path=${encodeURIComponent(path)}`;

export const statusTone = (s?: string | null): Tone => ['confirmed', 'tuned'].includes(s || '') ? 'green' : 'grey';
export const statusWord = (L: Dict, s?: string | null) => (L.pl.st as Record<string, string>)[s || ''] || s || L.pl.st.none;
export const frameWord = (L: Dict, f?: string | null) => (L.pl.frame as Record<string, string>)[(f || '').toLowerCase()] || f || '';

const INK: Record<string, string> = { green: 'text-green-ink', amber: 'text-orange-ink', red: 'text-red-ink' };
export const inkOf = (color?: string | null) => INK[color || ''] || 'text-ink-3';

export const plain = (s?: string | null) => (s || '').replace(/`([^`]*)`/g, '$1').replace(/\*\*([^*]*)\*\*/g, '$1');

export const fullState = (f: FullProcess) => f.reverify ? 'reverify' : f.status;
export const showsVerification = (v: ProcLib, f: FullProcess) => !!v.verification || f.status === 'verified' || !!f.reverify;
export const fullTone = (v: ProcLib, f: FullProcess): Tone =>
  f.reverify ? 'orange' : f.status === 'verified' ? 'green' : v.verification && f.status === 'confirmed' ? 'grey' : statusTone(f.status);
export const fullWord = (L: Dict, v: ProcLib, f: FullProcess) =>
  showsVerification(v, f) ? (L.vf.st as Record<string, string>)[fullState(f)] || statusWord(L, f.status) : statusWord(L, f.status);
export const checkTone = (s?: string | null): Tone => s === 'next' ? 'green' : s === 'pending' || s === 'redo' ? 'orange' : 'grey';
export const waitWord = (L: Dict, f: FullProcess, title: (sub: string) => string) => {
  const c = f.run?.check;
  return c && c.state !== 'next' ? `${title(c.sub)} ${(L.vf.ck as Record<string, string>)[c.state] || c.state}` : '';
};
