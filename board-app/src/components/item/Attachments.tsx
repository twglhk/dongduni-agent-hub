import { useL } from '../../i18n/context';
import { fext, fileUrl, fname, IMG, TXT, VID } from '../../lib/base';
import { Icon } from '../Icon';
import { H3 } from './H3';

function FileRow({ p }: { p: string }) {
  const txt = TXT.test(p);
  return (
    <a href={fileUrl(p)} target={txt ? '_blank' : undefined} rel={txt ? 'noopener' : undefined} download={txt ? undefined : fname(p)}
      className="flex min-h-14 items-center gap-3 rounded-box bg-surface-3 px-4 text-ink no-underline">
      <Icon name={txt ? 'file' : 'dl'} className="size-[22px] text-blue" stroke={1.8} />
      <span className="min-w-0 flex-1 truncate text-t5 font-medium">{fname(p)}</span>
      <span className="text-t6 uppercase text-ink-3">{fext(p)}</span>
    </a>
  );
}

export function Attachments({ paths, onImage }: { paths: string[]; onImage: (imgs: string[], i: number) => void }) {
  const { L } = useL();
  const imgs = paths.filter(p => IMG.test(p)), vids = paths.filter(p => VID.test(p));
  const rest = paths.filter(p => !IMG.test(p) && !VID.test(p)), one = imgs.length === 1;
  return (
    <div>
      <H3>{L.label.attachments}<span className="font-medium text-ink-4">{paths.length}</span></H3>
      {imgs.length > 0 && (
        <div className={`grid gap-2 ${one ? 'grid-cols-1' : 'grid-cols-2 sm:grid-cols-3'}`}>
          {imgs.map((p, i) => (
            <button key={p} type="button" aria-label={fname(p)} onClick={() => onImage(imgs, i)}
              className={`block w-full overflow-hidden rounded-tile bg-media ${one ? '' : 'aspect-[4/3]'}`}>
              <img src={fileUrl(p)} alt={fname(p)} loading="lazy" className={`block size-full ${one ? 'max-h-[60vh] object-contain' : 'object-cover'}`} />
            </button>
          ))}
        </div>
      )}
      {vids.map(p => <video key={p} src={fileUrl(p) + '#t=0.001'} controls preload="metadata" playsInline
        className="mt-2 block max-h-[60vh] w-full rounded-tile bg-media" />)}
      {rest.length > 0 && <div className="mt-2 grid gap-2">{rest.map(p => <FileRow key={p} p={p} />)}</div>}
    </div>
  );
}
