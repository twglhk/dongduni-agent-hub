import { useEffect, useState } from 'react';
import { useL } from '../i18n/context';
import { fname } from '../lib/base';
import { attachKeys, attachKind, attachUrl, type View } from '../lib/attach';
import { useBoard } from '../state/board';

function One({ k, players }: { k: string; players: boolean }) {
  const { L } = useL();
  const [v, setV] = useState<View | null>(null);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    let live = true;
    attachUrl(k).then(x => { if (live) setV(x); }, () => { if (live) setBad(true); });
    return () => { live = false; };
  }, [k]);
  const kind = attachKind(k), url = v?.url;
  const chip = 'inline-flex min-h-8 items-center rounded-chip bg-surface-3 px-2.5 text-t6 font-semibold text-ink-2 no-underline break-all';
  if (bad) return <span className={chip}>{L.up.noView} · {fname(k)}</span>;
  if (kind === 'image') return <a href={url} target="_blank" rel="noopener" aria-label={fname(k)}>
    {v ? <img src={v.thumb || url} alt={fname(k)} loading="lazy" className="h-[88px] w-auto max-w-40 rounded-tile bg-media object-cover" /> : <span className="block h-[88px] w-28 rounded-tile bg-media" />}</a>;
  if (players && kind === 'video') return <video src={url} controls preload="metadata" playsInline className="block max-h-[50vh] w-full rounded-tile bg-media" />;
  if (players && kind === 'audio') return <audio src={url} controls preload="metadata" className="w-full" />;
  return <a href={url} target="_blank" rel="noopener" className={chip}>{(kind === 'video' ? '▶ ' : kind === 'audio' ? '♪ ' : '') + fname(k)}</a>;
}

export function AttachStrip({ text, players = false }: { text?: string | null; players?: boolean }) {
  const { data } = useBoard();
  const keys = attachKeys(text);
  if (!keys.length || !data?.uploads) return null;
  return <div className="mt-3 flex flex-wrap gap-2">{keys.map(k => <One key={k} k={k} players={players} />)}</div>;
}
