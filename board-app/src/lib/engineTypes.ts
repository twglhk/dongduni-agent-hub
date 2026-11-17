import type { Day } from './laneTypes';

export interface Rating { n: number; pct: number; state: string; color: string; fixes?: number; pending?: number }

export interface PipeStage {
  n: string; name: string; by?: string; weakest?: boolean;
  card: { status: string; version?: number | null; color?: string; path?: string | null } | null;
  rating: Rating | null;
}

export interface Pipeline {
  row: string; type: string; lane?: string | null; skill?: string | null; card?: string | null;
  version?: string | number | null; process_status?: string | null; pattern_status?: string | null; model?: string | null;
  rating: Rating | null; stages: PipeStage[];
}

export interface EngineSkill { name: string; files: number; lanes: string[]; shared: boolean; doc?: string | null }
export interface MapEntry { name: string; note?: string; status?: string }
export interface Change { kind: string; name: string; from?: string | null; to?: string | null }

export interface EngineMap {
  id: string; label: string; implicit: boolean; notes?: string;
  pipelines: Pipeline[]; skills: EngineSkill[]; tools: MapEntry[]; experiments: MapEntry[]; usage: Day[];
  changes: { since: number | null; items: Change[] };
}

export interface EngineInfo { id: string; label: string; lanes: string[]; rows: number; implicit: boolean }
