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
  doing?: string | null; task_id?: number | null; task_title?: string | null; model?: string | null; effort?: string | null;
  process?: string | null; stage?: string | number | null; fixing?: boolean; task_tokens?: Tokens | null; ctx_k?: number | null;
  usage?: Usage | null;
  requester?: string | null;
  owner?: string | null;
  base?: string | null;
  branch?: string | null;
}

export interface LaneCap { used: number; cap: number; max: number }

export interface Reply { text: string; created?: number }
export interface Memo { id: number; text: string; sender?: string; created?: number; delivered?: number | null; replies?: Reply[] }

export interface Task {
  id: number; lane: string; title: string; state: string; created?: number; started?: number | null; closed?: number | null;
}

export interface Report {
  id: number; lane: string; kind: string; text: string; attachments?: string[]; task_id?: number | null; created?: number;
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

export interface BoardState {
  title: string;
  lang: string;
  auth_mode: string;
  me: Me | null;
  my_hub?: string;
  my_hub_state?: MyHubState;
  requester?: boolean;
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
