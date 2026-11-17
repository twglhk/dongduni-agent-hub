export interface Option { label: string; desc?: string; recommended?: boolean }

export interface Item {
  id: number;
  lane: string;
  kind: string;
  title: string;
  body?: string;
  state: string;
  audience?: string;
  assignee?: string | null;
  hub?: string;
  question?: string | null;
  escalation?: string | null;
  options: Option[];
  multi: boolean;
  attachments: string[];
  verdict?: string | null;
  choice?: string | null;
  note?: string | null;
  decided_by?: string | null;
  decided?: number | null;
  created?: number;
}

export interface Tokens { new?: number; cache?: number; out?: number; turns?: number }

export interface Usage { tokens_in?: number; tokens_out?: number; cost?: number | null; sessions?: number }

export interface Lane {
  lane: string; label?: string; status: string; kind?: string; runtime?: string; engine?: string | null;
  hidden?: boolean; doing?: string | null; task_id?: number | null; task_title?: string | null; model?: string | null; effort?: string | null;
  process?: string | null; stage?: string | number | null; fixing?: boolean; task_tokens?: Tokens | null; ctx_k?: number | null;
  usage?: Usage | null;
  requester?: string | null;
  owner?: string | null;
  base?: string | null;
  branch?: string | null;
  sessions?: number | null;
  wait?: string | null;
  memo?: { id: number; kind: string; title: string } | null;
  account?: string | null;
  held?: string | null;
}

export interface LaneCap { used: number; cap: number; max: number; sessions_max?: number }

export interface Reply { text: string; created?: number }
export interface Memo { id: number; text: string; sender?: string; created?: number; delivered?: number | null; replies?: Reply[] }

export interface Task {
  id: number; hidden?: boolean; lane: string; title?: string; state: string; created?: number; started?: number | null; closed?: number | null;
}

export interface Report {
  id: number; hidden?: boolean; lane: string; kind: string; text?: string; attachments?: string[]; task_id?: number | null; created?: number;
}

export interface WorkReport { id: number; lane: string; kind: string; text: string; created: number; attachments: string[]; retro: { cost?: string; corrected?: string; rule?: string } | null }
export interface WorkLine { row: string; full: string | null; sub: string | null; stage?: string | null; mode?: string | null }
export interface WorkRow {
  src: 'task' | 'infra' | 'report'; id: number; kind?: string; item_id?: number | null; at: number; lane: string; title: string; brief: string;
  state: string; by: string | null; requester: string | null; paths: string[]; line?: WorkLine | null; started: number | null; ended: number | null;
  cards: { id: number; title: string; kind: string; state: string }[]; reports: WorkReport[]; replies?: { text: string; created: number }[];
  commits: string[]; files: string[];
}

export interface RuntimeUse { runtime: string; tokens_in: number; tokens_out: number; cost?: number | null }

export interface MyHubState {
  state: 'off' | 'starting' | 'full' | 'ready';
  hub: string;
  step?: 'opening' | 'reading';
  place?: number;
  waiting?: number;
  cap?: number;
}

export interface Me { name: string; role?: string }

export interface UploadsInfo { provider: 'local' | 'drive' | 's3'; max_mb: number; max_files: number; chunk: number; accept: string[] }
export interface QueueItem { id: number; title: string; kind: string; session?: number | null; who?: string | null; why?: string | null; hidden?: boolean }
export interface LaneQueue { now: QueueItem[]; wait: QueueItem[]; waiting: number; by_person: { who: string | null; n: number }[] }

export interface BoardState {
  title: string;
  lang: string;
  auth_mode: string;
  me: Me | null;
  my_hub?: string;
  queues?: Record<string, LaneQueue>;
  my_hub_state?: MyHubState;
  requester?: boolean;
  claude_accounts?: boolean;
  accounts?: { member: string; state: 'connected' | 'ended' | 'none' }[] | null;
  uploads?: UploadsInfo | null;
  running: boolean;
  version: string;
  local_only: boolean;
  paused?: boolean;
  extensions?: { css?: boolean; js?: boolean };
  lane_cap?: LaneCap | null;
  runtimes?: string[];
  default_runtime?: string;
  usage?: { day?: string; runtimes?: RuntimeUse[] } | null;
  lanes: Lane[];
  items: Item[];
  tasks: Task[];
  reports: Report[];
  memos: { hub: Memo[]; infra?: Memo[] };
  counts: Record<string, number>;
}

export interface Workspace { name: string; href: string | null; running: boolean; current?: boolean; open?: number }
