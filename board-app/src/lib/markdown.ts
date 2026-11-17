// A small safe subset of markdown. Every server string is escaped before any tag is added.
const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

const inline = (s: string) => esc(s).split(/(`[^`]+`)/).map((p, i) => i % 2 ? '<code>' + p.slice(1, -1) + '</code>'
  : p.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')).join('');

// `doc` is for whole files (lane documents): front matter is skipped, tables and wrapped list items are drawn.
// Card and memo text stays without them, as on the single-file board.
export function markdown(text: string | null | undefined, doc = false): string {
  const out: string[] = [];
  let para: string[] = [], quote: string[] = [], list: { t: string; items: string[] } | null = null, fence: string[] | null = null;
  let table: string[] | null = null, front = false;
  const flushP = () => { if (para.length) out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; };
  const flushQ = () => { if (quote.length) out.push('<blockquote>' + quote.map(inline).join('<br>') + '</blockquote>'); quote = []; };
  const flushL = () => { if (list) out.push(`<${list.t}>` + list.items.map(x => '<li>' + inline(x) + '</li>').join('') + `</${list.t}>`); list = null; };
  const flushT = () => {
    if (!table) return;
    const rows = table.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r))
      .map(r => r.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => inline(c.trim())));
    out.push('<div class="tbl"><table>' + rows.map((r, i) => '<tr>' + r.map(c => i ? `<td>${c}</td>` : `<th>${c}</th>`).join('') + '</tr>').join('') + '</table></div>');
    table = null;
  };
  const flush = () => { flushP(); flushQ(); flushL(); flushT(); };
  const item = (t: string, x: string) => { flushP(); flushQ(); flushT(); if (!list || list.t !== t) { flushL(); list = { t, items: [] }; } list.items.push(x); };
  String(text || '').split(/\r?\n/).forEach((raw, n) => {
    if (doc && n === 0 && raw.trim() === '---') { front = true; return; }
    if (front) { if (raw.trim() === '---') front = false; return; }
    if (fence) {
      if (/^\s*```/.test(raw)) { out.push('<pre><code>' + esc(fence.join('\n')) + '</code></pre>'); fence = null; } else fence.push(raw);
      return;
    }
    const line = raw.trimEnd();
    let m: RegExpExecArray | null;
    if (/^\s*```/.test(line)) { flush(); fence = []; }
    else if (!line.trim()) flush();
    else if (doc && /^\s*\|.*\|\s*$/.test(line)) { flushP(); flushQ(); flushL(); (table = table || []).push(line); }
    else if ((m = /^\s*>\s?(.*)$/.exec(line))) { flushP(); flushL(); flushT(); quote.push(m[1]); }
    else if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) { flush(); const t = m[1].length <= 2 ? 'h3' : 'h4'; out.push(`<${t}>${inline(m[2])}</${t}>`); }
    else if ((m = /^\s*[-*+]\s+(.*)$/.exec(line))) item('ul', m[1]);
    else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) item('ol', m[1]);
    else if (doc && list && /^\s{2,}\S/.test(line)) list.items[list.items.length - 1] += ' ' + line.trim();
    else { flushQ(); flushL(); flushT(); para.push(line); }
  });
  const open = fence as string[] | null;
  if (open) out.push('<pre><code>' + esc(open.join('\n')) + '</code></pre>');
  flush();
  return out.join('');
}
