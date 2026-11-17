import type { BoardState, Item } from './types';

export const isOpen = (it: Item) => it.state === 'open';
export const forHuman = (it: Item) => isOpen(it) && it.audience === 'human';
export const myHub = (d: BoardState) => d.my_hub || 'hub';
export const inMyHub = (d: BoardState, it: Item) => !it.hub || it.hub === 'hub' || it.hub === myHub(d);
export const forMe = (d: BoardState, it: Item) =>
  d.requester && it.assignee && d.me && d.auth_mode !== 'local' ? it.assignee === d.me.name : inMyHub(d, it);
export const newest = (a: Item, b: Item) => (b.created || 0) - (a.created || 0);

export const laneLabel = (d: BoardState | null, id: string) => {
  const l = d?.lanes.find(x => x.lane === id);
  return l ? l.label || l.lane : id;
};
