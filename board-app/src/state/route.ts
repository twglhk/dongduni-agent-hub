import { useEffect, useState } from 'react';

export const TABS = ['hub', 'engine', 'lanes', 'history', 'info'] as const;
export type Tab = (typeof TABS)[number];
export type View = Tab | `x-${string}`;
export interface Route { view: View; item: number | null; lane: string | null; full: string | null; sub: string | null }
const NONE = { item: null, lane: null, full: null, sub: null };

const X_VIEW = /^x-[A-Za-z0-9_-]+$/;
let pushed = false;
let lastView: View = 'hub';
let firstTab: Tab = 'hub';
export const setFirstTab = (t: Tab) => { firstTab = t; };

function parse(): Route {
  const hs = location.hash.replace(/^#/, '');
  const mi = /^item-(\d+)$/.exec(hs);
  if (mi) return { ...NONE, view: lastView, item: Number(mi[1]) };
  const ml = /^lane-([A-Za-z0-9_-]+)$/.exec(hs);
  if (ml) return { ...NONE, view: lastView, lane: ml[1] };
  const mp = /^(full|sub)-([a-z0-9-]+)$/.exec(hs);
  if (mp) return mp[1] === 'full' ? { ...NONE, view: lastView, full: mp[2] } : { ...NONE, view: lastView, sub: mp[2] };
  const view: View = (TABS as readonly string[]).includes(hs) ? hs as Tab : X_VIEW.test(hs) ? hs as View : /^engine-/.test(hs) ? 'engine' : hs ? 'hub' : firstTab;
  pushed = false;
  lastView = view;
  return { ...NONE, view };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => {
      const prev = lastView, next = parse();
      setR(next);
      if (!sheetOpen(next) && prev !== next.view) window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return r;
}

export const sheetOpen = (r: Route) => r.item !== null || !!(r.lane || r.full || r.sub);
export const go = (view: View) => { location.hash = view; };
export const openItem = (id: number) => { pushed = true; location.hash = 'item-' + id; };
export const openLane = (id: string) => { pushed = true; location.hash = 'lane-' + id; };
export const openFull = (id: string) => { pushed = true; location.hash = 'full-' + id; };
export const openSub = (id: string) => { pushed = true; location.hash = 'sub-' + id; };
export const closeItem = () => { if (pushed) { pushed = false; history.back(); } else location.hash = lastView; };
