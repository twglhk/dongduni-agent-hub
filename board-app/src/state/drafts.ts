import { useSyncExternalStore } from 'react';

export interface Draft { choice: string | null; picks: string[]; note: string; busy: boolean }
const EMPTY: Draft = { choice: null, picks: [], note: '', busy: false };

const drafts = new Map<number, Draft>();
const subs = new Set<() => void>();
const emit = () => subs.forEach(f => f());
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };

export function setDraft(id: number, patch: Partial<Draft>) { drafts.set(id, { ...(drafts.get(id) || EMPTY), ...patch }); emit(); }
export function dropDraft(id: number) { drafts.delete(id); emit(); }
export const useDraft = (id: number) => useSyncExternalStore(subscribe, () => drafts.get(id) || EMPTY);
