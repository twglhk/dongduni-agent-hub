import type { Lane } from './types';

export const baseOf = (l: Lane) => l.base || l.lane;

export function byLane(lanes: Lane[]): Lane[][] {
  const out: Lane[][] = [];
  for (const l of lanes) {
    const last = out[out.length - 1];
    if (last && l.base && baseOf(last[0]) === l.base) last.push(l); else out.push([l]);
  }
  return out;
}
