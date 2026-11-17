import type { ReactNode } from 'react';
import { useL } from '../../i18n/context';
import { LANGS, type Lang } from '../../i18n/dict';
import { useBoard } from '../../state/board';
import { useToast } from '../../state/toast';
import { Icon } from '../Icon';
import { ClaudeBody, ClaudeCta } from '../account/ClaudeSheet';
import { NewLaneBody, NewLaneCta } from '../lanes/NewLaneSheet';
import { PrefsBody } from '../prefs/PrefsSheet';
import { SheetHead, type SheetContent } from '../Sheet';

export type Menu = 'lang' | 'ws' | 'prefs' | 'newlane' | 'claude';

function Row({ on, disabled, onClick, children }: { on?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} aria-current={on ? 'true' : undefined}
      className={`flex min-h-[60px] w-full items-center gap-3 rounded-box px-4 py-3 text-left disabled:cursor-default disabled:opacity-50 ${on ? 'bg-blue-weak' : 'bg-surface-3'}`}>
      {children}
    </button>
  );
}

function LangMenu({ close }: { close: () => void }) {
  const { L, lang, setLang } = useL();
  const toast = useToast();
  return (
    <>
      <div className="grid gap-2" role="listbox">
        {(Object.entries(LANGS) as [Lang, string][]).map(([c, nm]) => (
          <Row key={c} on={c === lang} onClick={() => { if (!setLang(c)) toast(L.msg.fail, true); close(); }}>
            <div className="min-w-0 flex-1" lang={c}>
              <div className={`text-t5 font-semibold leading-snug ${c === lang ? 'text-blue-ink' : ''}`}>{nm}</div>
              <div className="mt-0.5 text-t6 text-ink-3">{c.toUpperCase()}</div>
            </div>
            {c === lang && <Icon name="check" className="size-5 text-blue" stroke={3} />}
          </Row>
        ))}
      </div>
      <div className="px-1 pt-2 text-t6 leading-normal text-ink-3">{L.lang.note}</div>
    </>
  );
}

function WsMenu({ close }: { close: () => void }) {
  const { L } = useL();
  const { ws } = useBoard();
  return (
    <div className="grid gap-2">
      {ws.map(w => (
        <Row key={w.name} on={w.current} disabled={!w.href} onClick={w.href && !w.current ? () => { location.href = w.href!; } : close}>
          <i className={`inline-block size-2 flex-none rounded-full ${w.running ? 'bg-green' : 'shadow-[inset_0_0_0_1.5px_var(--text-4)]'}`} />
          <div className="min-w-0 flex-1">
            <div className={`text-t5 font-semibold leading-snug ${w.current ? 'text-blue-ink' : ''}`}>{w.name}</div>
            <div className="mt-0.5 truncate text-t6 text-ink-3">{w.href ? w.running ? L.ws.running : L.ws.stopped : L.ws.local}</div>
          </div>
          {!!w.open && <span className="h-5 min-w-5 rounded-full bg-red px-1.5 text-center text-t6 leading-5 tabular-nums font-bold text-white">{w.open}</span>}
          {w.current && <Icon name="check" className="size-5 text-blue" stroke={3} />}
        </Row>
      ))}
    </div>
  );
}

export function menuSheet(menu: Menu, L: ReturnType<typeof useL>['L'], close: () => void): SheetContent {
  const title = menu === 'lang' ? L.lang.title : menu === 'ws' ? L.ws.title : menu === 'newlane' ? L.nl.title : menu === 'claude' ? L.ca.title : L.pf.title;
  const head = <SheetHead onClose={close}><span className="flex-1 text-t4 font-bold">{title}</span></SheetHead>;
  if (menu === 'newlane') return { id: 'm-newlane', label: title, head, body: <NewLaneBody />, cta: <NewLaneCta close={close} /> };
  if (menu === 'claude') return { id: 'm-claude', label: title, head, side: true, body: <ClaudeBody />, cta: <ClaudeCta close={close} /> };
  if (menu === 'prefs') return {
    id: 'm-prefs', label: title, head, side: true, body: <PrefsBody />,
    cta: <button type="button" onClick={close} className="flex h-14 w-full items-center justify-center rounded-box bg-grey-weak text-t5 font-semibold text-ink-2 active:scale-[.98]">{L.btn.close}</button>,
  };
  return { id: 'm-' + menu, label: title, head, body: menu === 'lang' ? <LangMenu close={close} /> : <WsMenu close={close} /> };
}
