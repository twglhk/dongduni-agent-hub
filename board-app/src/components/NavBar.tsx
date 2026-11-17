import { useEffect, useRef } from 'react';
import { xLabel, xTabs, useXVersion } from '../ext/registry';
import { useL } from '../i18n/context';
import { go, TABS, type View } from '../state/route';
import { Icon } from './Icon';
import { UseBar } from './UseBar';

// An extension may hand over a DOM node as its icon; it is cloned into the button.
function NodeIcon({ node }: { node: Node }) {
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => { box.current?.replaceChildren(node.cloneNode(true)); }, [node]);
  return <span ref={box} aria-hidden="true" className="flex size-6 items-center justify-center [&>svg]:size-6 [&>svg]:fill-none [&>svg]:stroke-current" />;
}

// The tab bar: the built-in tabs, then the ones an extension added. The Hub tab carries the count of cards waiting for you.
export function NavBar({ view, badge }: { view: View; badge: number }) {
  const { L, lang } = useL();
  useXVersion();
  const btn = 'relative flex h-[60px] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-t6 leading-[1.4] font-semibold transition-colors';
  return (
    <nav className="pb-safe fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface">
      <UseBar />
      <div className="mx-auto flex max-w-col">
        {TABS.map(t => (
          <button key={t} type="button" onClick={() => go(t)} aria-current={view === t ? 'page' : undefined}
            className={`${btn} ${view === t ? 'text-ink' : 'text-ink-4'}`}>
            <Icon name={t} className="size-6" stroke={1.8} />
            <span>{L.tabs[t]}</span>
            {t === 'hub' && badge > 0 && (
              <span className="absolute top-1 left-[calc(50%+4px)] h-5 min-w-5 rounded-full bg-red px-1.5 text-center text-t6 leading-5 tabular-nums font-bold text-white">{badge}</span>
            )}
          </button>
        ))}
        {xTabs().map(t => {
          const v = ('x-' + t.id) as View, name = xLabel(t.label, t.id, lang);
          return (
            <button key={t.id} type="button" onClick={() => go(v)} aria-current={view === v ? 'page' : undefined}
              className={`${btn} ${view === v ? 'text-ink' : 'text-ink-4'}`}>
              {t.icon && typeof t.icon === 'object' && 'nodeType' in t.icon ? <NodeIcon node={t.icon as Node} />
                : <Icon d={typeof t.icon === 'string' && t.icon.trim() ? t.icon : undefined} className="size-6" stroke={1.8} />}
              <span className="max-w-full truncate px-1">{name}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
