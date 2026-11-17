// The small DOM builders `window.dah` hands to extensions (dah.h, dah.svg, dah.sec, dah.empty). Extensions return DOM
// nodes, so these build nodes, not React elements.
type Kid = Node | string | number | false | null | undefined | readonly unknown[];
type Attrs = Record<string, unknown> | null | undefined;

export function h(tag: string, attrs?: Attrs, ...kids: Kid[]): HTMLElement {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') e.className = String(v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v as EventListener);
    else e.setAttribute(k, v === true ? '' : String(v));
  }
  for (const k of kids.flat(Infinity) as unknown[]) {
    if (k === null || k === undefined || k === false || k === '') continue;
    e.append(typeof k === 'object' && 'nodeType' in (k as object) ? k as Node : document.createTextNode(String(k)));
  }
  return e;
}

export function svg(d: string, cls?: string): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('class', 'size-5 flex-none fill-none stroke-current ' + (cls || ''));
  s.setAttribute('stroke-width', '2.2');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', d);
  s.append(p);
  return s;
}

// A section with the board's heading line: title, an optional count, then the body.
export function sec(title: string, n?: number | null, body?: Kid, ext?: Kid): HTMLElement {
  return h('section', { class: 'mb-8' },
    h('div', { class: 'mb-3 flex items-center gap-2 px-1 text-t3 font-bold leading-snug tracking-[-.01em]' },
      h('h2', null, title), n !== null && n !== undefined ? h('span', { class: 'text-t4 font-semibold text-ink-3' }, String(n)) : null,
      ext && [ext].flat().some(Boolean) ? h('span', { class: 'ml-auto' }, ext) : null),
    body);
}

export const empty = (text: string): HTMLElement =>
  h('div', { class: 'rounded-card bg-surface px-5 py-6 text-center text-t5 text-ink-3' }, text);
