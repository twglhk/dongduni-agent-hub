import { useCallback, useEffect, useRef, useState, type PointerEvent, type TouchEvent } from 'react';
import { createPortal } from 'react-dom';
import { useL } from '../../i18n/context';
import { DocText } from '../DocText';

const SLIDE_MS = 300;

// Pushes a history entry without touching the hash, so the phone back button closes the sheet and the router never sees it.
export function DocReader({ title, path, url, onClose }: { title: string; path: string; url: string; onClose: () => void }) {
  const { L } = useL();
  const [open, setOpen] = useState(false);
  const [dy, setDy] = useState(0);
  const sheet = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y0: number; t0: number } | null>(null);
  const done = useRef(false);

  const close = useCallback((fromHistory = false) => {
    if (done.current) return;
    done.current = true;
    if (!fromHistory && history.state?.docSheet) history.back();
    setDy(0); setOpen(false);
    setTimeout(onClose, SLIDE_MS);
  }, [onClose]);

  useEffect(() => {
    done.current = false;
    if (!history.state?.docSheet) history.pushState({ docSheet: true }, '');
    const pop = () => close(true);
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    window.addEventListener('popstate', pop);
    document.addEventListener('keydown', key, true);
    const r = requestAnimationFrame(() => { setOpen(true); sheet.current?.focus({ preventScroll: true }); });
    return () => { cancelAnimationFrame(r); window.removeEventListener('popstate', pop); document.removeEventListener('keydown', key, true); };
  }, [close]);

  useEffect(() => { if (body.current) body.current.scrollTop = 0; }, [url]);

  const start = (y: number, fromBody: boolean) => {
    if (fromBody && (body.current?.scrollTop || 0) > 0) return;
    drag.current = { y0: y, t0: Date.now() };
  };
  const move = (y: number) => { if (drag.current) setDy(Math.max(0, y - drag.current.y0)); };
  const end = () => {
    const g = drag.current;
    if (!g) return;
    drag.current = null;
    const h = sheet.current?.offsetHeight || 600;
    if (dy > Math.min(120, h / 3) || (dy > 40 && dy / Math.max(1, Date.now() - g.t0) > 0.5)) close(); else setDy(0);
  };
  const onTouchMove = (e: TouchEvent) => { if (drag.current && e.touches[0].clientY > drag.current.y0 && e.cancelable) e.preventDefault(); move(e.touches[0].clientY); };
  const onHeadDown = (e: PointerEvent) => { start(e.clientY, false); e.currentTarget.setPointerCapture(e.pointerId); };

  const dragging = dy > 0 && drag.current !== null;
  return createPortal(
    <>
      <div onClick={() => close()} className={`fixed inset-0 z-[59] bg-[var(--scrim)] transition-opacity duration-250 ${open ? 'opacity-100' : 'opacity-0'}`} />
      <div ref={sheet} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}
        style={{ transform: open ? `translateY(${dy}px)` : undefined, transition: dragging ? 'none' : undefined }}
        className={`fixed inset-x-0 bottom-0 z-[60] mx-auto flex max-h-[calc(100dvh-env(safe-area-inset-top)-40px)] max-w-col flex-col rounded-t-card bg-surface shadow-[var(--shadow)] outline-none transition-transform duration-300 ease-out-soft min-[760px]:bottom-6 min-[760px]:max-h-[calc(100vh-80px)] min-[760px]:rounded-card ${open ? '' : 'translate-y-[105%] min-[760px]:translate-y-[calc(100%+32px)]'}`}>
        <div onPointerDown={onHeadDown} onPointerMove={e => move(e.clientY)} onPointerUp={end} onPointerCancel={end}
          className="relative flex-none cursor-grab touch-none px-5 pt-5 pb-2">
          <span className="absolute top-[7px] left-1/2 h-1 w-9 -translate-x-1/2 rounded-sm bg-line-2" />
          <b className="block text-t4 leading-snug">{title}</b>
          <span className="mt-0.5 block truncate text-t6 text-ink-3">{path}</span>
        </div>
        <div ref={body} onTouchStart={e => start(e.touches[0].clientY, true)} onTouchMove={onTouchMove} onTouchEnd={end}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-2 pb-4"><DocText url={url} /></div>
        <div className="flex-none border-t border-line px-5 pt-2 pb-[calc(12px+env(safe-area-inset-bottom))] min-[760px]:pb-3">
          <button type="button" onClick={() => close()} className="h-[52px] w-full rounded-tile bg-grey-weak text-t5 font-semibold text-ink-2">{L.btn.close}</button>
        </div>
      </div>
    </>,
    document.body,
  );
}
