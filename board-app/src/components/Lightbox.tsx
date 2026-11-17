import { useEffect, useRef } from 'react';
import { useL } from '../i18n/context';
import { fileUrl, fname } from '../lib/base';
import { Icon } from './Icon';

export interface LbState { list: string[]; i: number }

export function Lightbox({ lb, setLb }: { lb: LbState | null; setLb: (x: LbState | null) => void }) {
  const { L } = useL();
  const start = useRef<{ x: number; y: number } | null>(null);
  const step = (n: number) => { if (lb && lb.list.length > 1) setLb({ ...lb, i: (lb.i + n + lb.list.length) % lb.list.length }); };

  useEffect(() => {
    if (!lb) return;
    const key = (e: KeyboardEvent) => {
      e.stopPropagation();
      if (e.key === 'Escape') setLb(null); else if (e.key === 'ArrowRight') step(1); else if (e.key === 'ArrowLeft') step(-1);
    };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  });

  if (!lb) return null;
  const p = lb.list[lb.i], many = lb.list.length > 1;
  const navBtn = 'absolute top-1/2 hidden size-12 -translate-y-1/2 items-center justify-center rounded-full bg-white/15 text-white [@media(hover:hover)_and_(pointer:fine)]:flex';
  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-40 flex touch-pan-y flex-col bg-black/95 text-white"
      onTouchStart={e => { start.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }; }}
      onTouchEnd={e => {
        if (!start.current) return;
        const dx = e.changedTouches[0].clientX - start.current.x, dy = e.changedTouches[0].clientY - start.current.y;
        start.current = null;
        if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
      }}>
      <div className="flex items-center pt-[calc(env(safe-area-inset-top)+4px)] pr-2 pb-1 pl-5 text-t5">
        <span className="flex-1 font-semibold tabular-nums text-white/75">{lb.i + 1} / {lb.list.length}</span>
        <button type="button" aria-label={L.btn.close} onClick={() => setLb(null)} className="flex w-11 items-center justify-center"><Icon name="close" className="size-[22px]" /></button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center px-2" onClick={e => { if (e.target === e.currentTarget) setLb(null); }}>
        {many && <button type="button" aria-label={L.btn.prev} onClick={() => step(-1)} className={`${navBtn} left-4`}><Icon name="prev" className="size-[22px]" /></button>}
        <img src={fileUrl(p)} alt={fname(p)} className="max-h-full max-w-full select-none object-contain" />
        {many && <button type="button" aria-label={L.btn.next} onClick={() => step(1)} className={`${navBtn} right-4`}><Icon name="next" className="size-[22px]" /></button>}
      </div>
      <div className="flex items-center gap-3 px-5 pt-2 pb-[calc(12px+env(safe-area-inset-bottom))] text-t6">
        <span className="flex-1 truncate text-white/60">{fname(p)}</span>
        <a href={fileUrl(p)} target="_blank" rel="noopener" className="inline-flex min-h-11 items-center font-semibold text-white no-underline">{L.btn.open}</a>
      </div>
    </div>
  );
}
