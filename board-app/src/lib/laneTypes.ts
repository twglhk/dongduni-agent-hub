import type { Tokens } from './types';

export interface Stage { n: string | number; name: string }

export interface Day { day: string; tokens_in: number; tokens_out: number }

export interface LaneDetail {
  lane: string; label?: string; kind: string; runtime: string; engine?: string | null; model?: string | null; status: string;
  skills: { name: string; found: boolean }[];
  task: { id: number; title: string; state: string; model?: string | null; effort?: string | null; tokens?: Tokens | null } | null;
  process: { type: string; version?: string | number | null; status?: string | null; stages: Stage[]; now?: string | number | null } | null;
  fixing: { by: string; type?: string | null; stage?: string | number | null; task_id: number } | null;
  days: Day[];
  recent: { id: number; title: string; state: string; model?: string | null; effort?: string | null; tokens?: Tokens | null }[];
}
