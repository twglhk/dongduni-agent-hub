import { BASE } from './base';
import { joinBase } from './path';

export class HttpError extends Error {
  constructor(public status: number, msg: string) { super(msg); }
}

type Texts = { net: string; fail: string; badToken: string };
let texts: Texts = { net: 'Offline', fail: 'Couldn’t send', badToken: 'Wrong token' };
let onUnauthorized = () => {};

export function configureApi(t: Texts, unauthorized: () => void) { texts = t; onUnauthorized = unauthorized; }

const tagged = new Map<string, { tag: string; body: unknown }>();

async function req<T>(path: string, opt?: RequestInit): Promise<T> {
  const url = joinBase(BASE, path), isGet = !opt?.method || opt.method === 'GET', seen = isGet ? tagged.get(url) : undefined;
  let r: Response;
  try {
    r = await fetch(url, { cache: 'no-store', ...opt, headers: { ...(seen ? { 'if-none-match': seen.tag } : {}), ...(opt?.headers as Record<string, string> | undefined) } });
  } catch { throw new HttpError(0, texts.net); }
  if (r.status === 304 && seen) return seen.body as T;
  const j: any = await r.json().catch(() => null);
  const tag = isGet && r.ok ? r.headers.get('etag') : null;
  if (tag) tagged.set(url, { tag, body: j });
  if (r.status === 401) { onUnauthorized(); throw new HttpError(401, j?.error || texts.badToken); }
  if (!r.ok) throw new HttpError(r.status, j?.error || `${texts.fail} (${r.status})`);
  return j as T;
}

export const get = <T>(path: string) => req<T>(path);

// A custom header forces a CORS preflight, so other sites cannot post.
export const post = <T>(path: string, body?: unknown) => req<T>(path, {
  method: 'POST', headers: { 'x-tower': '1', 'content-type': 'application/json' }, body: JSON.stringify(body || {}),
});
