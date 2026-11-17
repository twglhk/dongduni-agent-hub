import { useSyncExternalStore } from 'react';
import { BASE } from '../lib/base';
import { get, post } from '../lib/api';
import type { Dict } from '../i18n/dict';
import type { BoardState } from '../lib/types';
import { TABS } from '../state/route';
import { empty, h, sec, svg } from './dom';

// A workspace extends the board from .tower/: the server reports extensions.{css,js} in api/state and serves the files
// at x/<path>. extra.js talks to the board only through window.dah (see guide/EXTEND.md):
//   dah.addSection(tab, id, render)      painted after that tab's own sections
//   dah.addTab(id, label, icon, render)  a tab after the built-in ones, opened at #x-<id>
//   dah.onState(fn)                      fn(state) after every state load, and once right after registering
// render(state) returns a Node, a string, an array of those, or null.
export type Render = (state: BoardState | null) => unknown;
export interface XSection { id: string; render: Render }
export interface XTab { id: string; label: unknown; icon: unknown; render: Render }

const X_ID = /^[A-Za-z0-9_-]+$/;
const secs = new Map<string, Map<string, XSection>>();
const tabs = new Map<string, XTab>();
const states: ((s: BoardState | null) => void)[] = [];
let version = 0;
let started = false;
const subs = new Set<() => void>();

// What the React side lends window.dah: the current state, language and the board's own helpers.
export const bridge: { data: BoardState | null; lang: string; L: Dict | null; refresh: () => void; toast: (m: string, err?: boolean) => void } =
  { data: null, lang: 'en', L: null, refresh: () => {}, toast: () => {} };

const bump = () => { version++; subs.forEach(f => f()); };
const bad = (msg: string) => { console.error('[dah] ' + msg); return false; };
const call = (fn: (s: BoardState | null) => void) => { try { fn(bridge.data); } catch (e) { console.error('[dah] onState callback failed:', e); } };

export const useXVersion = () => useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => version);
export const xSections = (tab: string) => [...(secs.get(tab)?.values() || [])];
export const xTabs = () => [...tabs.values()];
export const xTab = (view: string) => /^x-[A-Za-z0-9_-]+$/.test(view) ? tabs.get(view.slice(2)) || null : null;

export function xLabel(label: unknown, id: string, lang: string): string {
  if (typeof label === 'string' && label) return label;
  if (label && typeof label === 'object') {
    const o = label as Record<string, unknown>;
    return String(o[lang] || o.en || Object.values(o).find(v => typeof v === 'string') || id);
  }
  return id;
}

// What a render returned, as text: equal text means equal output, so the painted DOM can stay.
export function xKey(x: unknown): string {
  if (x === null || x === undefined || x === false || x === '') return '';
  if (Array.isArray(x)) return x.map(xKey).join('\u0001');
  const n = x as Node;
  if (n.nodeType === 1) return '<' + (n as Element).outerHTML;
  if (n.nodeType === 11) return xKey([...n.childNodes]);
  if (n.nodeType) return '#' + n.textContent;
  return '"' + String(x);
}

export function xNodes(x: unknown): Node[] {
  if (x === null || x === undefined || x === false || x === '') return [];
  if (Array.isArray(x)) return x.flatMap(xNodes);
  const n = x as Node;
  return n.nodeType ? [n] : [document.createTextNode(String(x))];
}

function addSection(tab: string, id: string, fn: Render) {
  if (!(TABS as readonly string[]).includes(tab)) return bad(`addSection: unknown tab ${JSON.stringify(tab)}; use one of ${TABS.join(', ')}`);
  if (typeof id !== 'string' || !id) return bad('addSection: id must be a non-empty string');
  if (typeof fn !== 'function') return bad(`addSection(${JSON.stringify(id)}): render must be a function`);
  const m = secs.get(tab) || secs.set(tab, new Map()).get(tab)!;
  m.set(id, { id, render: fn });
  bump();
  return true;
}

function addTab(id: string, label: unknown, icon?: unknown, fn?: Render) {
  if (typeof icon === 'function' && fn === undefined) { fn = icon as Render; icon = null; }
  if (typeof id !== 'string' || !X_ID.test(id)) return bad(`addTab: id ${JSON.stringify(id)} must be letters, numbers, - and _`);
  if (typeof fn !== 'function') return bad(`addTab(${JSON.stringify(id)}): render must be a function`);
  tabs.set(id, { id, label, icon, render: fn });
  bump();
  return true;
}

function onState(fn: (s: BoardState | null) => void) {
  if (typeof fn !== 'function') return bad('onState: fn must be a function');
  states.push(fn);
  if (bridge.data) setTimeout(() => call(fn), 0);  // once right after registering
  return true;
}

// Called by the bridge after every successful state load, changed or not.
export function xLoaded() {
  if (!bridge.data) return;
  for (const fn of states) call(fn);
}

// After the first state load the board adds x/extra.css and x/extra.js, once.
export function xStart(ext?: { css?: boolean; js?: boolean }) {
  if (started) return;
  started = true;
  if (ext?.css) document.head.append(h('link', { rel: 'stylesheet', href: BASE + 'x/extra.css' }));
  if (ext?.js) document.body.append(h('script', { src: BASE + 'x/extra.js', onerror: () => console.error('[dah] could not load ' + BASE + 'x/extra.js') }));
}

const url = (p: string) => BASE + String(p || '').replace(/^\/+/, '');
const API = {
  get version() { return bridge.data?.version || ''; },
  BASE: BASE.replace(/\/$/, ''),
  get lang() { return bridge.lang; },
  get L() { return bridge.L; },
  // Only `data` (the server state) is kept from the single-file board's state object.
  get S() { return { data: bridge.data }; },
  h, svg, sec, empty,
  toast: (m: string, err?: boolean) => bridge.toast(m, err),
  get: (p: string) => get(url(p)),
  post: (p: string, body?: unknown) => post(url(p), body),
  refresh: () => bridge.refresh(),
  addSection, addTab, onState,
};
(window as unknown as { dah: typeof API }).dah = API;
