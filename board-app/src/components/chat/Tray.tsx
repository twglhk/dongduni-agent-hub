import { useL } from '../../i18n/context';
import type { Pending } from '../../lib/uploads';
import { Icon } from '../Icon';

const BAR = { '': 'bg-blue', ok: 'bg-green', bad: 'bg-red' };

export function Tray({ files, busy, onDrop }: { files: Pending[]; busy: boolean; onDrop: (i: number) => void }) {
  const { L } = useL();
  if (!files.length) return null;
  const media = 'flex h-14 w-[72px] items-center justify-center rounded-[8px] bg-media object-cover text-t2 text-ink-3';
  return (
    <div className="no-scrollbar flex gap-2 overflow-x-auto px-1 pt-1">
      {files.map((u, i) => (
        <div key={u.url} title={u.file.name} className="relative grid w-[72px] flex-none gap-0.5">
          {u.kind === 'image' ? <img src={u.url} alt={u.file.name} className={media} />
            : u.kind === 'video' ? <video src={u.url} muted preload="metadata" playsInline className={media} />
            : u.kind === 'audio' ? <span aria-hidden="true" className={media}>♪</span>
            : <span aria-hidden="true" className={media}><Icon name="file" className="size-6" /></span>}
          <span className="truncate text-t6 text-ink-3">{u.file.name}</span>
          {u.state === 'bad' && <span role="alert" className="truncate text-t6 font-semibold text-red">{L.up.failed}</span>}
          <span className="h-[3px] overflow-hidden rounded-sm bg-line">
            <i className={`block h-full transition-[width] duration-200 ${BAR[u.state]}`} style={{ width: (u.state === 'bad' ? 100 : u.pct) + '%' }} />
          </span>
          {!busy && <button type="button" aria-label={`${L.up.remove}: ${u.file.name}`} onClick={() => onDrop(i)}
            className="absolute -top-1 -right-1 flex size-6 items-center justify-center rounded-full bg-toast text-toast-fg">
            <Icon name="close" className="size-3" stroke={3} /></button>}
        </div>
      ))}
    </div>
  );
}
