export const relPath = (p: unknown): string => String(p ?? '').replace(/^\/+/, '');
export const joinBase = (base: string, p: unknown): string => base + relPath(p);
export const encodePath = (p: unknown): string => String(p).split('/').map(encodeURIComponent).join('/');
