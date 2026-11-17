import { useRef, useState } from 'react';
import { useL } from '../i18n/context';
import { HttpError, post } from '../lib/api';
import { isSendKey, SEND_KEYS } from '../lib/keys';
import { useBoard } from '../state/board';
import { useToast } from '../state/toast';
import { KbdHint } from './KbdHint';

export function ChatBox() {
  const { L } = useL();
  const { refresh } = useBoard();
  const toast = useToast();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);

  const grow = () => { const el = ta.current; if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; } };
  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      await post('api/memo', { to: 'hub', text: t });
      setText('');
      if (ta.current) ta.current.style.height = '';
      toast(L.msg.sent);
      refresh();
    } catch (e) { if ((e as HttpError).status !== 401) toast((e as Error).message, true); }
    setBusy(false);
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--nav-h)+10px)] z-[19]">
      <div className="pointer-events-auto relative mx-4 flex max-w-[calc(720px-32px)] items-end gap-2 rounded-[26px] border border-line bg-surface p-1.5 shadow-[0_6px_24px_rgba(0,0,0,.14),0_1px_3px_rgba(0,0,0,.08)] focus-within:shadow-[0_0_0_1.5px_var(--blue),0_6px_24px_rgba(0,0,0,.14)] sm:mx-auto">
        <textarea ref={ta} rows={1} value={text} placeholder={L.chat.title} aria-label={L.chat.title}
          onChange={e => { setText(e.target.value); grow(); }}
          onKeyDown={e => { if (isSendKey(e)) { e.preventDefault(); send(); } }}
          className="h-11 max-h-[120px] min-w-0 flex-1 resize-none rounded-[22px] border-0 bg-transparent px-3 py-[11px] text-base leading-tight outline-none" />
        <button type="button" disabled={!text.trim() || busy} onClick={send} aria-keyshortcuts={SEND_KEYS}
          className="h-11 flex-none whitespace-nowrap rounded-[22px] bg-blue px-5 text-t5 font-bold text-white transition-[opacity,transform] active:enabled:scale-[.97] active:enabled:bg-blue-dn disabled:cursor-default disabled:opacity-35">
          {L.btn.send}<KbdHint />
        </button>
      </div>
    </div>
  );
}
