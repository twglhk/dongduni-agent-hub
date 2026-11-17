import { TABS, type Tab } from '../state/route';

export const FONTS = ['s', 'm', 'l'] as const;
export const ACCENTS = ['blue', 'green', 'orange', 'purple', 'pink'] as const;
export const SECTIONS = { hub: ['asks', 'yours'], lanes: ['lanes', 'tasks', 'reports'] } as const;
export type Font = (typeof FONTS)[number];
export type Accent = (typeof ACCENTS)[number];
export type SecTab = keyof typeof SECTIONS;
export type SecId<T extends SecTab = SecTab> = (typeof SECTIONS)[T][number];
export interface SecSpec { order: string[]; collapsed: string[]; hidden: string[] }
export interface Prefs { first_tab: Tab; font: Font; accent: Accent; seen: string[]; sections: Record<SecTab, SecSpec> }
export type Saved = Partial<Omit<Prefs, 'sections'>> & { sections?: Partial<Record<SecTab, Partial<SecSpec>>> };
export type Change = { [K in keyof Omit<Prefs, 'sections'>]?: Prefs[K] | null } & { sections?: Partial<Record<SecTab, Partial<SecSpec>>> };

export const DEFAULTS: Prefs = {
  first_tab: 'hub', font: 'm', accent: 'blue', seen: [],
  sections: { hub: { order: [], collapsed: [], hidden: [] }, lanes: { order: [], collapsed: [], hidden: [] } },
};

export const DEFAULT_MODE = new URLSearchParams(location.search).has('default');

export function effective(saved: Saved): Prefs {
  const p: Prefs = { ...DEFAULTS, ...saved, sections: { hub: { ...DEFAULTS.sections.hub }, lanes: { ...DEFAULTS.sections.lanes } } } as Prefs;
  for (const t of Object.keys(SECTIONS) as SecTab[]) Object.assign(p.sections[t], saved.sections?.[t] || {});
  if (!(TABS as readonly string[]).includes(p.first_tab)) p.first_tab = 'hub';
  return p;
}

export function merge(saved: Saved, change: Change): Saved {
  const out: Saved = { ...saved, sections: { ...saved.sections } };
  for (const [k, v] of Object.entries(change)) {
    if (k === 'sections') {
      for (const [t, spec] of Object.entries(v as object)) out.sections![t as SecTab] = { ...out.sections![t as SecTab], ...spec };
    } else if (v === null) delete (out as Record<string, unknown>)[k];
    else (out as Record<string, unknown>)[k] = v;
  }
  if (!Object.keys(out.sections!).length) delete out.sections;
  return out;
}

export function ordered<T extends SecTab>(p: Prefs, tab: T): SecId<T>[] {
  const all = SECTIONS[tab] as readonly string[];
  const first = p.sections[tab].order.filter(x => all.includes(x));
  return [...first, ...all.filter(x => !first.includes(x))] as SecId<T>[];
}

export const isHidden = (p: Prefs, tab: SecTab, id: string) => p.sections[tab].hidden.includes(id);

const KEY = 'tower.prefs';
let memory: Saved = {};
export function cached(): Saved {
  if (DEFAULT_MODE) return {};
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return memory; }
}
export function cache(saved: Saved) {
  memory = saved;
  try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch { return; }
}

export function applyLook(p: Prefs) {
  const el = document.documentElement;
  el.dataset.font = p.font;
  el.dataset.accent = p.accent;
}
