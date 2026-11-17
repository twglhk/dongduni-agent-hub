export interface Disk { mount: string; total_gb?: number | null; used_gb?: number | null; free_gb?: number | null; used_pct?: number | null; lv?: string | null }

export interface MachineInfo {
  machine: {
    lv?: string; cores?: number; ram_gb?: number; load_per_core?: number; cpu?: number; free_gb?: number; swap_gb?: number;
    load_lv?: string; cpu_lv?: string; mem_lv?: string; swap_lv?: string;
    disks?: Disk[];
  };
  waits?: Record<string, string>;
  usage?: { unknown?: boolean; windows?: { name: string; percent?: number | null }[] } | null;
}

export const isNum = (v: unknown): v is number => typeof v === 'number' && isFinite(v);
export const num = (v: unknown, nd = 0) => isNum(v) ? (nd ? v.toFixed(nd) : String(Math.round(v))) : null;

export function tok(n?: number | null) {
  const v = Math.max(0, Number(n) || 0);
  const r = (x: number) => String(x >= 100 ? Math.round(x) : Math.round(x * 10) / 10);
  return v < 1000 ? String(Math.round(v)) : v < 999500 ? r(v / 1e3) + 'K' : r(v / 1e6) + 'M';
}

export const cost = (c: number) => c > 0 && c < 0.01 ? '<$0.01' : '$' + c.toFixed(2);

export const RUNTIMES: Record<string, string> = { opencode: 'OpenCode', claude: 'Claude Code', codex: 'Codex' };
export const rtName = (r?: string) => (r && RUNTIMES[r]) || r || '';
