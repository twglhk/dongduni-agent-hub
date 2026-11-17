export interface Skill { name: string; found: boolean; doc: string | null }
export interface Rating { n: number; pct: number; state: string; color: string; fixes?: number; weakest?: boolean }
export interface PatternRef { path: string; exists: boolean; version?: number | null; status: string; color?: string }

export interface SubStage {
  n: string; name: string; input: string; output: string; gate: string; by: string;
  pattern: PatternRef | null; skills: Skill[];
}

export interface SubProcess {
  id: string; title: string; version?: number | null; status: string; pattern_status: string; confirmed: boolean; file: string;
  used_by_full: string[]; used_by_lanes: string[]; skills: Skill[]; stages: SubStage[];
}

export interface FullStep {
  n: number; frame: string; sub: string; what: string; model: string; known: boolean; title: string | null; status: string | null;
  rating?: Rating | null; stages?: Record<string, Partial<Rating>>;
}

export interface Verified { card: number; by: string | null; at: string; run: string; commit?: string }
export interface RunCheck { item_id: number; state: 'pending' | 'next' | 'redo' | 'hold'; member: string | null; sub: string }
export interface Run { key: string; sub: string; n: number | null; check: RunCheck | null; held: boolean }

export interface FullProcess {
  id: string; title: string; deliverable: string; frame_exception: string; file: string; lanes: string[]; status: string;
  steps: FullStep[]; verified?: Verified | null; reverify?: boolean; changes?: string[]; run?: Run | null;
}

export interface ProcLib {
  full: FullProcess[]; sub: SubProcess[]; lanes: Record<string, string[]>; problems: string[]; verification?: boolean;
}
