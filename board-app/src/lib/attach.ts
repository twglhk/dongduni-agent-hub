import { get } from './api';
import { BASE, fext } from './base';

export const UP_EXT: Record<'image' | 'video' | 'audio', RegExp> = {
  image: /^(jpe?g|png|gif|webp|heic|heif|avif)$/, video: /^(mp4|mov|webm|m4v|3gp)$/, audio: /^(mp3|m4a|aac|wav|ogg|flac|weba)$/,
};
export type AttachKind = keyof typeof UP_EXT | 'file';
export const attachKind = (name: string): AttachKind => (Object.keys(UP_EXT) as (keyof typeof UP_EXT)[]).find(k => UP_EXT[k].test(fext(name).toLowerCase())) || 'file';

const KEYRE = /(?<![A-Za-z0-9._-])(uploads\/\d{4}-\d{2}-\d{2}\/\d+\/\d+-[A-Za-z0-9_-]+\.[a-z0-9]+)/g;
export const attachKeys = (text?: string | null) => [...new Set([...String(text || '').matchAll(KEYRE)].map(m => m[1]))];

export interface View { url: string; thumb?: string; type: string }

const cache: Record<string, Promise<View>> = {};
export function attachUrl(key: string): Promise<View> {
  return cache[key] ||= get<View>('api/uploads/view?key=' + encodeURIComponent(key)).then(
    v => ({ ...v, url: BASE + v.url, thumb: v.thumb && BASE + v.thumb }),
    e => { delete cache[key]; throw e; });
}
