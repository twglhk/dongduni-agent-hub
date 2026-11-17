export interface SegOpt { id: string; label: string; n?: number }

// A row of pill tabs inside a view (engines, history sections).
export function Seg({ opts, cur, onPick }: { opts: SegOpt[]; cur: string; onPick: (id: string) => void }) {
  return (
    <div role="tablist" className="no-scrollbar mb-4 flex gap-1.5 overflow-x-auto px-1">
      {opts.map(o => {
        const on = o.id === cur;
        return (
          <button key={o.id} type="button" role="tab" aria-selected={on} onClick={() => { if (!on) onPick(o.id); }}
            className={`h-9 flex-none rounded-[18px] px-3.5 text-t5 font-semibold whitespace-nowrap ${on ? 'bg-blue text-white' : 'bg-surface text-ink-2'}`}>
            {o.label}{o.n !== undefined && <span className={`ml-1 font-medium tabular-nums ${on ? 'text-white/80' : 'text-ink-4'}`}>{o.n}</span>}
          </button>
        );
      })}
    </div>
  );
}
