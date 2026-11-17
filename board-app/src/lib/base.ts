export const BASE = location.pathname.replace(/[^/]*$/, '');

export const fileUrl = (p: string) => BASE + 'f/' + encodeURI(String(p));
export const fname = (p: string) => String(p).split('/').pop() || p;
export const fext = (p: string) => /\.([a-z0-9]+)$/i.exec(p)?.[1] ?? '';

export const IMG = /\.(png|jpe?g|gif|webp|avif|svg)$/i;
export const VID = /\.(mp4|webm|mov|m4v)$/i;
export const TXT = /\.(md|txt|json|log)$/i;
