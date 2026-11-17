import type { BoardState, Item } from './types';

export const isOpen = (it: Item) => it.state === 'open';
export const forHuman = (it: Item) => isOpen(it) && it.audience === 'human';
export const newest = (a: Item, b: Item) => (b.created || 0) - (a.created || 0);

export const laneLabel = (d: BoardState | null, id: string) => {
  const l = d?.lanes.find(x => x.lane === id);
  return l ? l.label || l.lane : id;
};
