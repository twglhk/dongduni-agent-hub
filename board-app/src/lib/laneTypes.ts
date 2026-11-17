import type { Tokens } from './types';

export interface SessionWork {
  kind: 'memo' | 'decision' | 'task'; id: number; title: string; body: string; from: string | null; hub: string | null;
  request: number | null; item: number | null; started: number | null; ended: number | null; live: boolean;
}

export interface SessionView {
  key: string; live: boolean; hidden: boolean; work: SessionWork | null;
  report: { kind: string; text: string; created: number | null } | null;
  account?: string | null; held?: string | null; runtime?: string;
}

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
