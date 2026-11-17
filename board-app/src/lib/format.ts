import type { Dict } from '../i18n/dict';

export const fmt = (s: string, v: Record<string, unknown>) => String(s).replace(/\{(\w+)\}/g, (_, k) => String(v[k] ?? ''));

export function fmtT(s?: number | null) {
  if (!s) return '';
  const d = new Date(s * 1000), p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function rel(L: Dict, s?: number | null) {
  if (!s) return '';
  const dt = Date.now() / 1000 - s;
  if (dt < 60) return L.time.now;
  if (dt < 3600) return fmt(L.time.m, { n: Math.floor(dt / 60) });
  if (dt < 86400) return fmt(L.time.h, { n: Math.floor(dt / 3600) });
  return fmtT(s);
}
