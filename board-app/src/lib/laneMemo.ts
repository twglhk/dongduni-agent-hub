export const LANE_ID = /^[A-Za-z0-9_-]+$/;

export interface LaneForm { id: string; runtime: string; label: string; model: string; agent: string; role: string }

// The memo the hub gets: the template's lines for empty optional fields are left out.
export function laneMemo(template: string, f: LaneForm): string {
  const arg = (s: string) => /^[\w./:@+-]+$/.test(s) ? s : JSON.stringify(s);
  const v: Record<string, string> = { ...f };
  v.flags = (f.label ? ` --label ${JSON.stringify(f.label)}` : '') + (f.model ? ` --model ${arg(f.model)}` : '') + (f.agent ? ` --agent ${arg(f.agent)}` : '');
  return template.split('\n')
    .filter(ln => !(['label', 'model', 'agent'] as const).some(k => !f[k] && ln.includes(`{${k}}`)))
    .map(ln => ln.replace(/\{(\w+)\}/g, (_, k) => v[k] ?? '')).join('\n');
}
