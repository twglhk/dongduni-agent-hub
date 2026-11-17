import { BASE } from './base';

export class HttpError extends Error {
  constructor(public status: number, msg: string) { super(msg); }
}

type Texts = { net: string; fail: string; badToken: string };
let texts: Texts = { net: 'Offline', fail: 'Couldn’t send', badToken: 'Wrong token' };
let onUnauthorized = () => {};

export function configureApi(t: Texts, unauthorized: () => void) { texts = t; onUnauthorized = unauthorized; }

async function req<T>(path: string, opt?: RequestInit): Promise<T> {
  let r: Response;
  try { r = await fetch(BASE + path, { cache: 'no-store', ...opt }); }
  catch { throw new HttpError(0, texts.net); }
  let j: any = null;
  try { j = await r.json(); } catch { /* not JSON */ }
  if (r.status === 401) { onUnauthorized(); throw new HttpError(401, j?.error || texts.badToken); }
  if (!r.ok) throw new HttpError(r.status, j?.error || `${texts.fail} (${r.status})`);
  return j as T;
}

export const get = <T>(path: string) => req<T>(path);

// A custom header forces a CORS preflight, so other sites cannot post.
export const post = <T>(path: string, body?: unknown) => req<T>(path, {
  method: 'POST', headers: { 'x-tower': '1', 'content-type': 'application/json' }, body: JSON.stringify(body || {}),
});
