import { useL } from '../i18n/context';
import { useToast } from '../state/toast';
import { post } from '../lib/api';
import { useBoard } from '../state/board';
import { Chip } from './Chips';
import { Icon } from './Icon';
import { StatusDot } from './lane/StatusChip';
import type { Menu } from './menus/MenuSheet';

export function TopBar({ openMenu }: { openMenu: (m: Menu) => void }) {
  const { L, lang } = useL();
  const { data, ok, ws, lock } = useBoard();
  const toast = useToast();
  const name = data?.title || '', many = ws.length > 1;
  const logout = async () => {
    try { await post('api/logout'); } catch (e) { toast((e as Error).message || L.msg.fail, true); return; }
    lock();
  };
  return (
    <header className="pt-safe sticky top-0 z-20 border-b border-line bg-surface">
      <div className="mx-auto flex min-h-11 max-w-col items-center gap-2 px-5 pt-1 pb-1">
        <h1 className="min-w-0 truncate text-t4 font-bold tracking-[-.01em]">
          {many ? (
            <button type="button" aria-haspopup="dialog" aria-label={`${name} · ${L.ws.title}`} onClick={() => openMenu('ws')}
              className="inline-flex max-w-full min-w-0 items-center gap-0.5 rounded-chip pr-0.5">
              <span className="truncate">{name}</span><Icon name="down" className="size-[18px] text-ink-3" />
            </button>
          ) : name}
        </h1>
        {ok === false && <Chip tone="red">{L.msg.net}</Chip>}
        {data && data.running === false && <Chip tone="orange">{L.msg.stopped}</Chip>}
        <span className="flex-1" />
        <span className="max-w-[40vw] truncate whitespace-nowrap text-t6 font-semibold text-ink-3">{data?.me?.name || ''}</span>
        <button type="button" aria-haspopup="dialog" aria-label={L.pf.title} title={L.pf.title} onClick={() => openMenu('prefs')}
          className="-mr-1 flex size-11 flex-none items-center justify-center rounded-full text-ink-3 active:bg-[var(--press)]"><Icon name="tune" className="size-[22px]" stroke={2} /></button>
        <button type="button" aria-haspopup="dialog" aria-label={L.lang.title} onClick={() => openMenu('lang')}
          className="min-w-11 whitespace-nowrap rounded-chip px-2 text-t6 font-bold tracking-wider text-ink-3">{lang.toUpperCase()}</button>
        {data?.auth_mode === 'token' && (
          <button type="button" onClick={logout} className="whitespace-nowrap rounded-chip px-2 text-t6 font-semibold text-ink-3">{L.btn.logout}</button>
        )}
      </div>
      {data && data.lanes.some(l => !l.base) && (
        <div className="no-scrollbar mx-auto flex max-w-col gap-1.5 overflow-x-auto px-5 pb-2" aria-label={L.sec.lanes}>
          {data.lanes.filter(l => !l.base).map(l => (
            <span key={l.lane} title={(L.status as Record<string, string>)[l.status] || l.status}
              className="inline-flex h-7 flex-none items-center gap-1.5 whitespace-nowrap rounded-[14px] bg-grey-weak px-2.5 text-t6 font-semibold text-ink-2">
              <StatusDot status={l.status} />{l.label || l.lane}
            </span>
          ))}
        </div>
      )}
    </header>
  );
}
