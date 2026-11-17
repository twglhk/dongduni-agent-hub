import { useSyncExternalStore } from 'react';

export interface DocRef { title: string; path: string; url: string }

let cur: DocRef | null = null;
const subs = new Set<() => void>();
const emit = () => subs.forEach(f => f());

export const openDoc = (d: DocRef) => { cur = d; emit(); };
export const closeDoc = () => { cur = null; emit(); };
export const useDoc = () => useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => cur);
