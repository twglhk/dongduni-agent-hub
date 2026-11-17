import type { Tone } from '../components/Chips';
import type { Dict } from '../i18n/dict';

export const procDocUrl = (path: string) => `api/processes/doc?path=${encodeURIComponent(path)}`;

export const statusTone = (s?: string | null): Tone => ['confirmed', 'tuned'].includes(s || '') ? 'green' : 'grey';
export const statusWord = (L: Dict, s?: string | null) => (L.pl.st as Record<string, string>)[s || ''] || s || L.pl.st.none;
export const frameWord = (L: Dict, f?: string | null) => (L.pl.frame as Record<string, string>)[(f || '').toLowerCase()] || f || '';

const INK: Record<string, string> = { green: 'text-green-ink', amber: 'text-orange-ink', red: 'text-red-ink' };
export const inkOf = (color?: string | null) => INK[color || ''] || 'text-ink-3';

export const plain = (s?: string | null) => (s || '').replace(/`([^`]*)`/g, '$1').replace(/\*\*([^*]*)\*\*/g, '$1');
