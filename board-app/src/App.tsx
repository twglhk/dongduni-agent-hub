import { useCallback, useEffect, useState } from 'react';
import { ChatBox } from './components/ChatBox';
import { ExtensionBridge } from './components/ExtensionBridge';
import { itemSheet } from './components/item/ItemSheet';
import { Lightbox, type LbState } from './components/Lightbox';
import { Login } from './components/Login';
import { menuSheet, type Menu } from './components/menus/MenuSheet';
import { NavBar } from './components/NavBar';
import { Empty } from './components/Section';
import { DocReader } from './components/lane/DocReader';
import { Sheet, type SheetContent } from './components/Sheet';
import { TopBar } from './components/TopBar';
import { XTabView } from './components/XHost';
import { useL } from './i18n/context';
import { HttpError } from './lib/api';
import { forHuman, forMe } from './lib/items';
import { useBoard } from './state/board';
import { closeDoc, useDoc } from './state/docSheet';
import { closeItem, sheetOpen, useRoute } from './state/route';
import { useHubWatch } from './state/hubWatch';
import { useOpenAccount } from './state/claude';
import { useOpenPrefs } from './state/prefs';
import { useToast } from './state/toast';
import { useLaneSheet } from './state/useLaneSheet';
import { useSessionSheet } from './state/useSessionSheet';
import { useProcSheet } from './state/useProcSheet';
import { EngineView } from './views/EngineView';
import { HistoryView } from './views/HistoryView';
import { HubView } from './views/HubView';
import { InfoView } from './views/InfoView';
import { LanesView } from './views/LanesView';

export function App() {
  const { L } = useL();
  const { data, locked, itemById, fetchItem, loadWs } = useBoard();
  const toast = useToast();
  const r = useRoute();
  const [menu, setMenu] = useState<Menu | null>(null);
  const [lb, setLb] = useState<LbState | null>(null);
  const badge = data ? data.items.filter(it => forHuman(it) && forMe(data, it)).length : 0;
  const onImage = (list: string[], i: number) => setLb({ list, i });
  useHubWatch(data, r, useCallback(() => toast(L.hw.ready), [toast, L]));

  useEffect(() => { document.title = (badge ? `(${badge}) ` : '') + (data?.title || ''); }, [badge, data?.title]);
  useEffect(() => { setMenu(null); closeDoc(); }, [r]);

  const it = r.item !== null ? itemById(r.item) : undefined;
  useEffect(() => {
    if (r.item === null || it || !data) return;
    fetchItem(r.item).catch(e => {
      if ((e as HttpError).status === 401) return;
      toast((e as HttpError).status === 404 ? L.msg.notFound : (e as Error).message, true);
      closeItem();
    });
  }, [r.item, !!it, !!data]);

  const openMenu = (m: Menu) => { if (m === 'ws') loadWs(); setMenu(m); };
  useOpenPrefs(useCallback(() => setMenu('prefs'), []));
  useOpenAccount(useCallback(() => setMenu('claude'), []));
  const closeMenu = () => setMenu(null);
  const lane = useLaneSheet(r.lane);
  const session = useSessionSheet(r.session);
  const proc = useProcSheet(r.full, r.sub);
  const doc = useDoc();
  const content: SheetContent | null = menu ? menuSheet(menu, L, closeMenu) : it ? itemSheet(it, onImage) : session || lane || proc;

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = /TEXTAREA|INPUT/.test(document.activeElement?.tagName || '');
      if (e.key === 'Escape' && !typing) { if (doc) return; if (menu) closeMenu(); else if (sheetOpen(r)) closeItem(); }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  });

  const view = !data ? <Empty>{L.empty.loading}</Empty>
    : r.view === 'hub' ? <HubView d={data} />
    : r.view === 'lanes' ? <LanesView d={data} onImage={onImage} onAdd={() => openMenu('newlane')} />
    : r.view === 'engine' ? <EngineView />
    : r.view === 'history' ? <HistoryView d={data} onImage={onImage} />
    : r.view === 'info' ? <InfoView />
    : <XTabView view={r.view} />;

  return (
    <>
      <ExtensionBridge />
      <TopBar openMenu={openMenu} />
      <main className="mx-auto max-w-col px-5 pt-5 pb-[calc(var(--nav-h)+var(--chat-h)+32px)]">{view}</main>
      {data && <ChatBox />}
      <NavBar view={r.view} badge={badge} />
      <Sheet content={content} onClose={menu ? closeMenu : closeItem} />
      {doc && <DocReader key={doc.url} {...doc} onClose={closeDoc} />}
      <Lightbox lb={lb} setLb={setLb} />
      {locked && <Login />}
    </>
  );
}
