import type { Tone } from '../components/Chips';
import type { Pipeline } from './engineTypes';

export const RT_TONE: Record<string, Tone> = { green: 'green', amber: 'orange', red: 'red' };
export const rtTone = (c?: string | null): Tone => RT_TONE[c || ''] || 'grey';

export const cardColor = (word?: string | null) =>
  ['confirmed', 'tuned'].includes(word || '') ? 'green' : ['collecting', 'draft', 'reviewed'].includes(word || '') ? 'amber' : 'none';

export function pipeTitle(p: Pipeline): [string, string] {
  const i = p.type.indexOf(':');
  return i < 0 ? [p.type, ''] : [p.type.slice(0, i).trim(), p.type.slice(i + 1).trim()];
}

export const docUrl = (eid: string, path: string) => `api/engines/${encodeURIComponent(eid)}/doc?path=${encodeURIComponent(path)}`;
