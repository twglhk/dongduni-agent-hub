import { useEffect, useState } from 'react';
import { useL } from '../i18n/context';
import { get } from '../lib/api';
import { fmt } from '../lib/format';
import { Prose } from './Prose';
import { Empty } from './Section';

// The text of one read-only document; the server decides which paths it serves.
export function DocText({ url }: { url: string }) {
  const { L } = useL();
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    setText(null); setErr('');
    get<{ text: string }>(url).then(j => { if (live) setText(j.text); }, e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [url]);
  if (err) return <Empty>{fmt(L.doc.cantOpen, { e: err })}</Empty>;
  return text === null ? <Empty>{L.doc.loading}</Empty> : <Prose text={text} doc className="small" />;
}
