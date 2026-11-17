import { useL } from '../i18n/context';

const BAR = 'block rounded-md bg-surface-3';

// Grey bars in the content's shape while a real wait happens, in the shape of what is coming.
export function SkelLines({ widths = [70, 45, 60, 50] }: { widths?: number[] }) {
  return (
    <div aria-hidden className="mt-3 grid gap-4">
      {widths.map((w, i) => (
        <div key={i} className="grid gap-2 py-1">
          <i className={`${BAR} h-4`} style={{ width: w + '%' }} />
          <i className={`${BAR} h-3 w-1/3`} />
        </div>
      ))}
    </div>
  );
}

// Cards like the history and engine lists: a chip line, a title, a meta line; `rows` adds list rows inside each card.
export function SkelCards({ n = 4, rows = 0 }: { n?: number; rows?: number }) {
  const { L } = useL();
  return (
    <div role="status" aria-label={L.empty.loading} className="grid grid-cols-1 gap-2">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} aria-hidden className="grid gap-2.5 rounded-card bg-surface px-5 py-4">
          <div className="flex gap-2"><i className={`${BAR} h-6 w-14 rounded-chip`} /><i className={`${BAR} h-6 w-10 rounded-chip`} /></div>
          <i className={`${BAR} h-5`} style={{ width: [72, 58, 66, 50][i % 4] + '%' }} />
          <i className={`${BAR} h-3.5 w-1/3`} />
          {Array.from({ length: rows }, (_, k) => (
            <div key={k} className="flex min-h-12 items-center gap-3"><i className={`${BAR} size-4`} /><i className={`${BAR} h-4 flex-1`} /><i className={`${BAR} h-3.5 w-10`} /></div>
          ))}
        </div>
      ))}
    </div>
  );
}
