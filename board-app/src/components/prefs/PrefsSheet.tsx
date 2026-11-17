import type { ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { fmt } from '../../lib/format';
import { ACCENTS, DEFAULT_MODE, FONTS, ordered, SECTIONS, type Prefs, type SecTab } from '../../lib/prefs';
import { TABS } from '../../state/route';
import { usePrefs } from '../../state/prefs';
import { Empty } from '../Section';
import { Icon } from '../Icon';
import { Seg } from '../Seg';
import { secName } from './names';

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6 first:mt-2">
      <h3 className="mb-2 px-1 text-t5 font-semibold text-ink-2">{title}</h3>
      {children}
    </section>
  );
}

type Show = 'open' | 'fold' | 'hide';
const showOf = (p: Prefs, tab: SecTab, id: string): Show =>
  p.sections[tab].hidden.includes(id) ? 'hide' : p.sections[tab].collapsed.includes(id) ? 'fold' : 'open';

function Sections({ tab }: { tab: SecTab }) {
  const { L } = useL();
  const { p, set } = usePrefs();
  const order = ordered(p, tab);
  const spec = p.sections[tab];
  const move = (i: number, d: number) => {
    const o = [...order];
    [o[i], o[i + d]] = [o[i + d], o[i]];
    set({ sections: { [tab]: { order: o } } });
  };
  const show = (id: string, v: Show) => set({ sections: { [tab]: {
    hidden: v === 'hide' ? [...spec.hidden.filter(x => x !== id), id] : spec.hidden.filter(x => x !== id),
    collapsed: v === 'fold' ? [...spec.collapsed.filter(x => x !== id), id] : spec.collapsed.filter(x => x !== id),
  } } });
  return (
    <div className="grid gap-2">
      {order.map((id, i) => {
        const name = secName(L, tab, id), cur = showOf(p, tab, id);
        return (
          <div key={id} className="rounded-box bg-surface-3 px-4 pt-3 pb-2">
            <div className="flex items-center gap-1">
              <span className={`min-w-0 flex-1 text-t5 font-semibold ${cur === 'hide' ? 'text-ink-3' : ''}`}>{name}</span>
              <button type="button" aria-label={fmt(L.pf.up, { name })} disabled={i === 0} onClick={() => move(i, -1)}
                className="flex size-11 items-center justify-center rounded-full text-ink-2 active:bg-[var(--press)] disabled:opacity-30">
                <Icon name="down" className="size-5 rotate-180" /></button>
              <button type="button" aria-label={fmt(L.pf.down, { name })} disabled={i === order.length - 1} onClick={() => move(i, 1)}
                className="-mr-2 flex size-11 items-center justify-center rounded-full text-ink-2 active:bg-[var(--press)] disabled:opacity-30">
                <Icon name="down" className="size-5" /></button>
            </div>
            <div role="radiogroup" aria-label={name} className="mt-1 grid grid-cols-3 gap-1 rounded-tile bg-surface p-1">
              {(['open', 'fold', 'hide'] as Show[]).map(v => (
                <button key={v} type="button" role="radio" aria-checked={cur === v} onClick={() => { if (cur !== v) show(id, v); }}
                  className={`h-11 rounded-chip text-t6 font-semibold ${cur === v ? 'bg-blue-weak text-blue-ink' : 'text-ink-2'}`}>{L.pf.show[v]}</button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PrefsBody() {
  const { L } = useL();
  const { p, ok, set, reset } = usePrefs();
  if (ok === false) return <Empty><div className="font-semibold text-ink">{L.pf.noMember}</div><div className="mt-1">{L.pf.noMemberSub}</div></Empty>;
  if (DEFAULT_MODE) {
    const mine = location.pathname + location.hash;
    return (
      <div className="rounded-box bg-surface-3 px-5 py-5">
        <div className="text-t4 font-bold">{L.pf.defOn}</div>
        <div className="mt-1 text-t5 leading-normal text-ink-2">{L.pf.defSub}</div>
        <a href={mine} className="mt-4 flex h-12 items-center justify-center rounded-box bg-blue-weak text-t5 font-semibold text-blue-ink">{L.pf.defOff}</a>
      </div>
    );
  }
  return (
    <>
      <p className="px-1 text-t6 leading-normal text-ink-3">{L.pf.live}</p>
      <Group title={L.pf.first}>
        <div className="-mx-1"><Seg opts={TABS.map(t => ({ id: t, label: L.tabs[t] }))} cur={p.first_tab} onPick={t => set({ first_tab: t as Prefs['first_tab'] })} /></div>
      </Group>
      <Group title={L.pf.font}>
        <div role="radiogroup" aria-label={L.pf.font} className="grid grid-cols-3 gap-2">
          {FONTS.map((f, i) => (
            <button key={f} type="button" role="radio" aria-checked={p.font === f} onClick={() => { if (p.font !== f) set({ font: f }); }}
              className={`flex h-14 items-center justify-center gap-1 rounded-box font-semibold ${p.font === f ? 'bg-blue-weak text-blue-ink' : 'bg-surface-3 text-ink-2'}`}>
              <span className={['text-t6', 'text-t5', 'text-t4'][i]}>{L.pf.sample}</span><span className="text-t6">{L.pf.fonts[f]}</span>
            </button>
          ))}
        </div>
      </Group>
      <Group title={L.pf.accent}>
        <div role="radiogroup" aria-label={L.pf.accent} className="flex flex-wrap gap-2">
          {ACCENTS.map(a => (
            <button key={a} type="button" role="radio" aria-checked={p.accent === a} aria-label={L.pf.accents[a]} onClick={() => { if (p.accent !== a) set({ accent: a }); }}
              className={`flex h-14 min-w-14 flex-1 flex-col items-center justify-center gap-1 rounded-box ${p.accent === a ? 'bg-surface-3 shadow-[inset_0_0_0_2px_var(--text-2)]' : 'bg-surface-3'}`}>
              <span data-swatch={a} className="flex size-6 items-center justify-center rounded-full">
                {p.accent === a && <Icon name="check" className="size-4 text-white" stroke={3} />}
              </span>
              <span className="text-t6 font-semibold text-ink-2">{L.pf.accents[a]}</span>
            </button>
          ))}
        </div>
      </Group>
      {(Object.keys(SECTIONS) as SecTab[]).map(t => <Group key={t} title={L.pf.secs[t]}><Sections tab={t} /></Group>)}
      <p className="mt-6 px-1 text-t6 leading-normal text-ink-3">{L.pf.chat}</p>
      <button type="button" onClick={reset}
        className="mt-3 flex h-12 w-full items-center justify-center rounded-box text-t5 font-semibold text-ink-2 active:bg-[var(--press)]">{L.pf.reset}</button>
    </>
  );
}
