import { useState } from 'react';
import { useL } from '../../i18n/context';
import { fileUrl, fname, IMG, TXT, VID } from '../../lib/base';
import type { Report } from '../../lib/types';
import { Chip, LaneChip, Meta, When, type Tone } from '../Chips';

export const REPORT_TONE: Record<string, Tone> = { done: 'green', blocked: 'red', handoff: 'teal', wrapped: 'teal', note: 'grey' };

// One report a lane sent: three lines until tapped; its images open in the viewer.
export function ReportCard({ r, onImage }: { r: Report; onImage: (imgs: string[], i: number) => void }) {
  const { L } = useL();
  const [open, setOpen] = useState(false);
  const atts = r.attachments || [], imgs = atts.filter(p => IMG.test(p));
  return (
    <div className="rounded-box bg-surface px-5 py-4">
      <Meta><LaneChip lane={r.lane} /><Chip tone={REPORT_TONE[r.kind] || 'grey'}>{(L.report as Record<string, string>)[r.kind] || r.kind}</Chip>
        {r.task_id ? <span>#{r.task_id}</span> : null}<When at={r.created} className="ml-auto" /></Meta>
      <div role="button" tabIndex={0} aria-expanded={open} onClick={() => setOpen(!open)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(!open); } }}
        className={`mt-2 cursor-pointer whitespace-pre-wrap [overflow-wrap:anywhere] text-t5 leading-[1.55] ${open ? '' : 'line-clamp-3'}`}>{r.text}</div>
      {atts.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{atts.map(p => IMG.test(p)
        ? <button key={p} type="button" aria-label={fname(p)} onClick={() => onImage(imgs, imgs.indexOf(p))}>
            <img src={fileUrl(p)} alt={fname(p)} loading="lazy" className="h-[72px] w-auto max-w-32 rounded-tile bg-media object-cover" /></button>
        : <a key={p} href={fileUrl(p)} target={TXT.test(p) || VID.test(p) ? '_blank' : undefined} rel="noopener"
            download={TXT.test(p) || VID.test(p) ? undefined : fname(p)} className="inline-flex min-h-8 items-center rounded-chip bg-surface-3 px-2.5 text-t6 font-semibold text-ink-2 no-underline break-all">{fname(p)}</a>)}</div>}
    </div>
  );
}
