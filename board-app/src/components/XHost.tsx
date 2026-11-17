import { useEffect, useRef, useState } from 'react';
import { xKey, xNodes, xSections, xTab, useXVersion, type Render } from '../ext/registry';
import { useL } from '../i18n/context';
import { useBoard } from '../state/board';
import { Icon } from './Icon';

// Paints what one extension render returns into its own div, and repaints only when the output changed, so scroll,
// focus and typed text inside it survive the board's polling. Returning the same Node again keeps it mounted.
function XHost({ name, render }: { name: string; render: Render }) {
  const { L } = useL();
  const { data } = useBoard();
  const box = useRef<HTMLDivElement>(null);
  const last = useRef('');
  const warned = useRef('');
  const [err, setErr] = useState('');
  useEffect(() => {
    const el = box.current;
    if (!el || err) return;
    try {
      const out = render(data);
      const node = out as Node | null;
      if (node && node.nodeType && el.childNodes.length === 1 && el.firstChild === node) last.current = '';
      else {
        const k = xKey(out);
        if (k !== last.current) { last.current = k; el.replaceChildren(...xNodes(out)); }
      }
    } catch (e) {
      const msg = String((e as Error)?.message || e || 'Error');
      if (warned.current !== msg) { warned.current = msg; console.error(`[dah] extension "${name}" failed:`, e); }
      setErr(msg);
    }
  });
  // A failing render is tried again when the data changes.
  useEffect(() => { setErr(''); }, [data, render]);
  if (err) return (
    <section role="alert" className="mb-8 grid gap-1 rounded-card bg-red-weak px-5 py-4">
      <div className="flex items-center gap-1.5 text-t6 font-semibold text-red-ink"><Icon name="warn" className="size-4" />{L.x.err}</div>
      <div className="text-t5 text-ink-2">{name} · {err}</div>
    </section>
  );
  return <div ref={box} data-x={name} />;
}

// Everything extensions added to one built-in tab, after that tab's own sections.
export function XSections({ tab }: { tab: string }) {
  useXVersion();
  const list = xSections(tab);
  if (!list.length) return null;
  return <>{list.map(s => <XHost key={s.id} name={s.id} render={s.render} />)}</>;
}

// The body of an extension tab (#x-<id>).
export function XTabView({ view }: { view: string }) {
  useXVersion();
  const { L } = useL();
  const t = xTab(view);
  if (!t) return <div className="rounded-card bg-surface px-5 py-6 text-center text-t5 text-ink-3">{L.empty.loading}</div>;
  return <XHost key={t.id} name={t.id} render={t.render} />;
}
