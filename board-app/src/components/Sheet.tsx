import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { useL } from '../i18n/context';
import { Icon } from './Icon';

export interface SheetContent { id: string; label: string; head: ReactNode; body: ReactNode; cta?: ReactNode; side?: boolean }

const WIDE = 'min-[760px]:bottom-6 min-[760px]:max-h-[calc(100vh-48px)] min-[760px]:rounded-card';
const SIDE = 'min-[760px]:inset-y-0 min-[760px]:right-0 min-[760px]:left-auto min-[760px]:m-0 min-[760px]:max-h-none min-[760px]:w-[400px] min-[760px]:max-w-[100vw] min-[760px]:rounded-none min-[760px]:rounded-l-card';

export function Sheet({ content, onClose }: { content: SheetContent | null; onClose: () => void }) {
  const [shown, setShown] = useState<SheetContent | null>(content);
  const [open, setOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const lastId = useRef('');
  const [dy, setDy] = useState(0);
  const drag = useRef<{ y0: number; t0: number } | null>(null);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('button') || (shown?.side && innerWidth >= 760)) return;
    drag.current = { y0: e.clientY, t0: Date.now() };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => { if (drag.current) setDy(Math.max(0, e.clientY - drag.current.y0)); };
  const up = () => {
    const d = drag.current; drag.current = null;
    if (d && (dy > 96 || (dy > 24 && dy / (Date.now() - d.t0) > 0.5))) onClose();
    setDy(0);
  };

  useEffect(() => {
    if (content) {
      setShown(content);
      const r = requestAnimationFrame(() => setOpen(true));
      return () => cancelAnimationFrame(r);
    }
    setOpen(false);
    const t = setTimeout(() => setShown(null), 340);
    return () => clearTimeout(t);
  }, [content]);

  useEffect(() => {
    if (shown && shown.id !== lastId.current && body.current) body.current.scrollTop = 0;
    lastId.current = shown?.id || '';
  }, [shown]);

  useEffect(() => { document.body.classList.toggle('sheet-open', open); }, [open]);

  return (
    <>
      <div onClick={onClose} className={`fixed inset-0 z-[29] bg-[var(--scrim)] transition-opacity duration-250 ${open ? 'opacity-100' : 'pointer-events-none opacity-0'}`} />
      <div role="dialog" aria-modal="true" aria-label={shown?.label} style={dy ? { transform: `translateY(${dy}px)`, transition: 'none' } : undefined}
        className={`fixed inset-x-0 bottom-0 z-30 mx-auto flex max-h-[calc(100dvh-env(safe-area-inset-top)-12px)] max-w-col flex-col rounded-t-card bg-surface shadow-[var(--shadow)] transition-[transform,visibility] duration-300 ease-out-soft ${shown?.side ? SIDE : WIDE} ${open ? 'visible translate-x-0 translate-y-0' : `invisible translate-y-[105%] ${shown?.side ? 'min-[760px]:translate-x-[105%] min-[760px]:translate-y-0' : 'min-[760px]:translate-y-[calc(100%+32px)]'}`}`}>
        {shown && <>
          <div onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} className={`flex-none touch-none ${shown.side ? 'min-[760px]:[&_.grab]:hidden' : ''}`}>{shown.head}</div>
          <div ref={body} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-1 pb-6">{shown.body}</div>
          {shown.cta && (
            <div className="relative flex-none bg-surface px-5 pt-2 pb-[calc(8px+env(safe-area-inset-bottom))] before:pointer-events-none before:absolute before:inset-x-0 before:-top-6 before:h-6 before:bg-linear-to-b before:from-[var(--surface-t)] before:to-[var(--surface)] min-[760px]:rounded-b-card min-[760px]:pb-4">
              {shown.cta}
            </div>
          )}
        </>}
      </div>
    </>
  );
}

export function SheetHead({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const { L } = useL();
  return (
    <div className="relative flex min-h-[58px] flex-none items-center gap-2 pt-3.5 pr-2 pb-0.5 pl-5">
      <span className="grab absolute top-[7px] left-1/2 h-1 w-9 -translate-x-1/2 rounded-sm bg-line-2" />
      {children}
      <button type="button" aria-label={L.btn.close} onClick={onClose}
        className="flex size-11 flex-none items-center justify-center rounded-full text-ink-3"><Icon name="close" className="size-[22px]" /></button>
    </div>
  );
}
