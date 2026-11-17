import { useEffect, useRef, useState } from 'react';
import { useL } from '../i18n/context';
import { HttpError, post } from '../lib/api';
import { fmt } from '../lib/format';
import { isSendKey, SEND_KEYS } from '../lib/keys';
import { accepts, pendingOf, sendWithFiles, type Pending } from '../lib/uploads';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';
import { Tray } from './chat/Tray';
import { Icon } from './Icon';
import { KbdHint } from './KbdHint';

export function ChatBox() {
  const { L } = useL();
  const { data, refresh } = useBoard();
  const toast = useToast();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<Pending[]>([]);
  const ta = useRef<HTMLTextAreaElement>(null), pick = useRef<HTMLInputElement>(null);
  const up = data?.uploads || null;

  useEffect(() => { document.body.classList.toggle('has-tray', files.length > 0); }, [files.length]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { if (busy && files.length) { e.preventDefault(); e.returnValue = L.up.leave; } };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [busy, files.length, L]);

  const grow = () => { const el = ta.current; if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; } };
  const clearText = () => { setText(''); if (ta.current) ta.current.style.height = ''; };
  const add = (list: File[]) => {
    if (!up) return;
    const next = [...files];
    for (const f of list) {
      if (!accepts(f, up)) { toast(fmt(L.up.badType, { name: f.name }), true); continue; }
      if (f.size > up.max_mb * 1048576) { toast(fmt(L.up.tooBig, { name: f.name, mb: up.max_mb }), true); continue; }
      if (next.length >= up.max_files) { toast(fmt(L.up.tooMany, { n: up.max_files }), true); break; }
      next.push(pendingOf(f));
    }
    setFiles(next);
  };
  const drop = (i: number) => { URL.revokeObjectURL(files[i].url); setFiles(files.filter((_, j) => j !== i)); };
  const send = async () => {
    const t = text.trim();
    if ((!t && !files.length) || busy) return;
    setBusy(true);
    try {
      if (files.length) {
        const r = await sendWithFiles(t, files, {
          sent: clearText, net: L.msg.net, fail: L.msg.fail, noEtag: L.up.noEtag,
          start: (i, n) => toast(fmt(L.up.run, { i, n })),
          step: (i, p) => setFiles(fs => fs.map((u, j) => j === i ? { ...u, ...p } : u)),
        });
        files.forEach(u => URL.revokeObjectURL(u.url));
        setFiles([]);
        toast(r.failed ? fmt(L.up.partial, { n: r.total, f: r.failed }) : fmt(L.up.done, { n: r.sent }), !!r.failed);
      } else {
        await post('api/memo', { to: 'hub', text: t });
        clearText();
        toast(L.msg.sent);
      }
      refresh();
    } catch (e) { if ((e as HttpError).status !== 401) toast((e as Error).message, true); }
    setBusy(false);
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--nav-h)+10px)] z-[19]">
      <div className="pointer-events-auto relative mx-4 grid max-w-[calc(720px-32px)] grid-cols-1 gap-1.5 rounded-[26px] border border-line bg-surface p-1.5 shadow-[0_6px_24px_rgba(0,0,0,.14),0_1px_3px_rgba(0,0,0,.08)] focus-within:shadow-[0_0_0_1.5px_var(--blue),0_6px_24px_rgba(0,0,0,.14)] sm:mx-auto">
        <Tray files={files} busy={busy} onDrop={drop} />
        <div className="flex items-end gap-2">
          {up && <>
            <button type="button" disabled={busy} aria-label={L.up.attach} title={L.up.attach} onClick={() => pick.current?.click()}
              className="flex size-11 flex-none items-center justify-center rounded-[22px] text-ink-2 disabled:opacity-40"><Icon name="clip" className="size-[22px]" stroke={2} /></button>
            <input ref={pick} type="file" multiple hidden accept={['image/*', 'video/*', 'audio/*', ...up.accept.map(x => '.' + x)].join(',')}
              onChange={e => { add([...(e.target.files || [])]); e.target.value = ''; }} />
          </>}
          <textarea ref={ta} rows={1} value={text} placeholder={L.chat.title} aria-label={L.chat.title}
            onChange={e => { setText(e.target.value); grow(); }}
            onKeyDown={e => { if (isSendKey(e)) { e.preventDefault(); send(); } }}
            className="h-11 max-h-[120px] min-w-0 flex-1 resize-none rounded-[22px] border-0 bg-transparent px-3 py-[11px] text-base leading-tight outline-none" />
          <button type="button" disabled={(!text.trim() && !files.length) || busy} onClick={send} aria-keyshortcuts={SEND_KEYS}
            className="h-11 flex-none whitespace-nowrap rounded-[22px] bg-blue px-5 text-t5 font-bold text-white transition-[opacity,transform] active:enabled:scale-[.97] active:enabled:bg-blue-dn disabled:cursor-default disabled:opacity-35">
            {L.btn.send}<KbdHint />
          </button>
        </div>
      </div>
    </div>
  );
}
