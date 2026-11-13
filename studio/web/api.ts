// The REST calls and the shapes `sprite-gen char --json` answers with (studio/server, docs/char.md).

export interface Spec {
  provider: string;
  model: string;
  resolution: string;
  direction: string;
  facing: string;
  key: string;
  body_plan: string;
  body_height: number;
  fps: number | null;
  walk_start: string;
}

export interface Busy {
  pid: number;
  what: string;
  since: string;
}

export interface VersionFiles {
  png?: string;
  json?: string;
  'meta.json'?: string;
  'align.png'?: string;
  clip?: string;
  clip_report?: string;
  log?: string;
}

export interface Version {
  version: number;
  status: 'running' | 'ok' | 'failed' | 'missing';
  ok: boolean | null;
  motion?: string;
  action_seconds?: number | null;
  started?: string;
  elapsed_seconds?: number;
  prompt_match?: boolean | null;
  seam?: number | null;
  error?: string | null;
  warnings?: string[] | null;
  stale?: boolean;
  files?: VersionFiles | null;
}

export interface Action {
  name: string;
  state: string;
  motion: string;
  action_seconds: number | null;
  current: number | null;
  current_stale: boolean;
  versions: Version[];
}

export interface Character {
  id: string;
  description: string;
  spec: Spec;
  spec_rev: number;
  ref: string;
  base: string;
  action_names: string[];
  busy: Busy | null;
  build: string | null;
  actions?: Action[];
}

export interface Job {
  id: string;
  char: string;
  action: string;
  kind: 'add' | 'regen';
  state: 'running' | 'done' | 'failed';
  startedAt: string;
  endedAt?: string;
  result?: Record<string, unknown> | null;
  stderrTail?: string;
}

export interface DryRun {
  dry_run: true;
  prompt: string;
  argv: string[];
  play?: unknown;
  action?: unknown;
  notes?: unknown;
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly body: Record<string, unknown>) {
    super(message);
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { 'Content-Type': 'application/json' };
  }
  const res = await fetch(url, init);
  let data: Record<string, unknown>;
  try {
    data = await res.json();
  } catch {
    throw new ApiError(res.status, `${method} ${url}: HTTP ${res.status}`, {});
  }
  if (!res.ok || data.ok === false) {
    const extra = typeof data.stderr === 'string' ? `\n${data.stderr}` : '';
    throw new ApiError(res.status, `${String(data.error ?? `HTTP ${res.status}`)}${extra}`, data);
  }
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  list: () => call<{ characters: Character[] }>('GET', '/api/chars'),
  show: (id: string) => call<{ character: Character; job: Job | null }>('GET', `/api/chars/${enc(id)}`),
  create: (form: FormData) => call<{ character: Character }>('POST', '/api/chars', form),
  spec: (id: string, body: Record<string, unknown>) =>
    call<{ changed: string[]; character: Character }>('PATCH', `/api/chars/${enc(id)}/spec`, body),
  addDry: (id: string, body: Record<string, unknown>) => call<DryRun>('POST', `/api/chars/${enc(id)}/actions`, { ...body, dryRun: true }),
  add: (id: string, body: Record<string, unknown>) => call<{ jobId: string; job: Job }>('POST', `/api/chars/${enc(id)}/actions`, { ...body, dryRun: false }),
  regenDry: (id: string, name: string, body: Record<string, unknown>) =>
    call<DryRun>('POST', `/api/chars/${enc(id)}/actions/${enc(name)}/regen`, { ...body, dryRun: true }),
  regen: (id: string, name: string, body: Record<string, unknown>) =>
    call<{ jobId: string; job: Job }>('POST', `/api/chars/${enc(id)}/actions/${enc(name)}/regen`, { ...body, dryRun: false }),
  use: (id: string, name: string, version: number) =>
    call<{ action: Action }>('POST', `/api/chars/${enc(id)}/actions/${enc(name)}/use`, { version }),
  rm: (id: string, name: string, purge: boolean) =>
    call<{ removed: string; trash: string | null; purged: boolean }>('DELETE', `/api/chars/${enc(id)}/actions/${enc(name)}${purge ? '?purge=1' : ''}`),
  build: (id: string) =>
    call<{ built: string[]; skipped: { action: string; why: string }[]; files: Record<string, string>; warnings: string[] }>('POST', `/api/chars/${enc(id)}/build`),
  job: (jobId: string) => call<{ job: Job }>('GET', `/api/jobs/${enc(jobId)}`),
  log: (id: string, name: string, n: number, offset: number) =>
    call<{ offset: number; size: number; text: string }>('GET', `/api/chars/${enc(id)}/actions/${enc(name)}/versions/${n}/log?offset=${offset}`),
  server: () => call<{ charsDir: string; fake: boolean }>('GET', '/api/server'),
};

/** A path from the CLI (relative to the chars dir, forward slashes) as a URL the server serves. */
export function fileUrl(rel: string, bust = false): string {
  return `/files/${rel.split('/').map(enc).join('/')}${bust ? `?t=${Date.now()}` : ''}`;
}

/** A static json under the chars dir (read-only: clip.report.json, version.json), or null when it is not there. */
export async function fileJson<T>(rel: string): Promise<T | null> {
  const res = await fetch(fileUrl(rel));
  if (!res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
