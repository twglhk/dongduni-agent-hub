import { useEffect, useState } from 'react';

export const TABS = ['hub', 'engine', 'lanes', 'history', 'info'] as const;
export type Tab = (typeof TABS)[number];
export type View = Tab | `x-${string}`;
export interface Route { view: View; item: number | null; lane: string | null }

const X_VIEW = /^x-[A-Za-z0-9_-]+$/;
let pushed = false;
let lastView: View = 'hub';

function parse(): Route {
  const hs = location.hash.replace(/^#/, '');
  const mi = /^item-(\d+)$/.exec(hs);
  if (mi) return { view: lastView, item: Number(mi[1]), lane: null };
  const ml = /^lane-([A-Za-z0-9_-]+)$/.exec(hs);
  if (ml) return { view: lastView, item: null, lane: ml[1] };
  const view: View = (TABS as readonly string[]).includes(hs) ? hs as Tab : X_VIEW.test(hs) ? hs as View : /^engine-/.test(hs) ? 'engine' : 'hub';
  pushed = false;
  lastView = view;
  return { view, item: null, lane: null };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => {
      const prev = lastView, next = parse();
      setR(next);
      if (!next.item && !next.lane && prev !== next.view) window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return r;
}

let lastEngine = '';
export const pickEngine = (id: string) => { lastEngine = id; location.hash = 'engine-' + id; };
export const go = (view: View) => { location.hash = view === 'engine' && lastEngine ? 'engine-' + lastEngine : view; };
export const openItem = (id: number) => { pushed = true; location.hash = 'item-' + id; };
export const openLane = (id: string) => { pushed = true; location.hash = 'lane-' + id; };
export const closeItem = () => { if (pushed) { pushed = false; history.back(); } else location.hash = lastView; };
