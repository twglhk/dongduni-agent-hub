import { HttpError, post } from './api';
import { attachKind, type AttachKind } from './attach';
import { BASE, fext } from './base';
import { joinBase } from './path';
import type { UploadsInfo } from './types';

export const UP_TRIES = 3;
const PART_BATCH = 20;

export interface Pending { file: File; kind: AttachKind; url: string; state: '' | 'ok' | 'bad'; pct: number }

export const accepts = (f: File, info: UploadsInfo) =>
  info.accept.includes(fext(f.name).toLowerCase()) || /^(image|video|audio)\//.test(f.type);

export const pendingOf = (f: File): Pending => ({ file: f, kind: attachKind(f.name), url: URL.createObjectURL(f), state: '', pct: 0 });

interface Reply { status: number; body: { error?: string; received?: number } | null; etag: string }

function xhr(method: string, url: string, blob: Blob | null, headers: Record<string, string>, onp: (n: number) => void, net: string) {
  return new Promise<Reply>((ok, no) => {
    const x = new XMLHttpRequest();
    x.open(method, url);
    for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
    x.upload.onprogress = e => onp(e.loaded);
    x.onload = () => {
      let body: Reply['body'] = null;
      try { body = JSON.parse(x.responseText); } catch { body = null; }
      ok({ status: x.status, body, etag: x.getResponseHeader('ETag') || '' });
    };
    x.onerror = () => no(new HttpError(0, net));
    x.send(blob);
  });
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const failed = (r: Reply, fail: string) => new HttpError(r.status, r.body?.error || `${fail} (${r.status})`);

interface Plan { n: number; mode: 'chunks' | 'put' | 'multipart'; chunk?: number; url?: string; headers?: Record<string, string>; part_size?: number; parts?: number }
interface Ctx { id: number; show: (n: number) => void; net: string; fail: string; noEtag: string }

async function viaBoard(f: File, plan: Plan, c: Ctx) {
  const url = (off: number) => joinBase(BASE, `api/uploads/chunk?id=${c.id}&n=${plan.n}&offset=${off}`);
  const head = { 'x-tower': '1', 'content-type': 'application/octet-stream' };
  const ask = async () => (await xhr('POST', url(0), null, head, () => {}, c.net)).body?.received ?? 0;
  let off = 0, fails = 0;
  while (off < f.size) {
    const at = off, end = Math.min(at + (plan.chunk || f.size), f.size);
    c.show(at);
    try {
      const r = await xhr('POST', url(at), f.slice(at, end), head, n => c.show(at + n), c.net);
      if (r.status === 200 && typeof r.body?.received === 'number') { off = r.body.received; fails = 0; continue; }
      if (r.status === 409 && typeof r.body?.received === 'number' && ++fails < UP_TRIES) { off = r.body.received; continue; }
      throw failed(r, c.fail);
    } catch (e) {
      if ((e as HttpError).status !== 0 || ++fails >= UP_TRIES) throw e;
      await sleep(1500 * fails);
      off = await ask();
    }
  }
}

async function putOnce(url: string, blob: Blob, headers: Record<string, string>, onp: (n: number) => void, c: Ctx) {
  for (let i = 1; ; i++) {
    try {
      const r = await xhr('PUT', url, blob, headers, onp, c.net);
      if (r.status < 300) return r;
      throw new HttpError(r.status, `${c.fail} (${r.status})`);
    } catch (e) {
      if (i >= UP_TRIES || [400, 401, 403, 404].includes((e as HttpError).status)) throw e;
      await sleep(1500 * i);
    }
  }
}

async function direct(f: File, plan: Plan, c: Ctx) {
  await putOnce(plan.url || '', f, plan.headers || {}, c.show, c);
}

async function multipart(f: File, plan: Plan, c: Ctx) {
  const size = plan.part_size || f.size, total = plan.parts || 1, etags: string[] = [];
  for (let first = 1; first <= total; first += PART_BATCH) {
    const numbers = Array.from({ length: Math.min(PART_BATCH, total - first + 1) }, (_, i) => first + i);
    const { urls } = await post<{ urls: Record<string, string> }>('api/uploads/parts', { id: c.id, n: plan.n, parts: numbers });
    for (const p of numbers) {
      const at = (p - 1) * size;
      const r = await putOnce(urls[p], f.slice(at, Math.min(at + size, f.size)), {}, n => c.show(at + n), c);
      if (!r.etag) throw new HttpError(0, c.noEtag);
      etags.push(r.etag);
    }
  }
  return etags;
}

async function tries<T>(fn: () => Promise<T>): Promise<T> {
  for (let i = 1; ; i++) {
    try { return await fn(); } catch (e) {
      const st = (e as HttpError).status;
      if (i >= UP_TRIES || (st >= 400 && st < 500)) throw e;
      await sleep(1500 * i);
    }
  }
}

export interface Sent { total: number; sent: number; failed: number }

export interface Hooks {
  sent: () => void;
  start: (i: number, n: number) => void;
  step: (i: number, p: Partial<Pending>) => void;
  net: string;
  fail: string;
  noEtag: string;
}

export async function sendWithFiles(text: string, files: Pending[], hooks: Hooks): Promise<Sent> {
  const plan = await post<{ id: number; files: Plan[] }>('api/uploads/start',
    { text, files: files.map(u => ({ name: u.file.name, type: u.file.type, size: u.file.size })) });
  hooks.sent();
  const results: { n: number; etags?: string[] }[] = [];
  for (const fp of plan.files) {
    const i = fp.n - 1, f = files[i].file;
    hooks.start(fp.n, files.length);
    const c: Ctx = { id: plan.id, net: hooks.net, fail: hooks.fail, noEtag: hooks.noEtag,
      show: n => hooks.step(i, { pct: Math.min(100, Math.round(n / f.size * 100)) }) };
    try {
      if (fp.mode === 'chunks') await viaBoard(f, fp, c);
      else if (fp.mode === 'put') await direct(f, fp, c);
      else results.push({ n: fp.n, etags: await multipart(f, fp, c) });
      hooks.step(i, { state: 'ok', pct: 100 });
    } catch { hooks.step(i, { state: 'bad' }); }
  }
  return tries(() => post<Sent>('api/uploads/finish', { id: plan.id, results }));
}
