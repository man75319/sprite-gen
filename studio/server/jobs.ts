// Paid runs (`char action add` / `regen` without --dry-run) take 2–4 minutes: they run as background jobs and the
// page polls them. One job per character here; the CLI's own lock (`<id>/.lock`) is the second line.
import { randomUUID } from 'node:crypto';
import { char, type CliResult } from './cli.ts';

export interface Job {
  id: string;
  char: string;
  action: string;
  kind: 'add' | 'regen';
  state: 'running' | 'done' | 'failed';
  startedAt: string;
  endedAt?: string;
  pid?: number;
  /** The CLI's JSON object once it ends. */
  result?: Record<string, unknown> | null;
  stderrTail?: string;
}

const jobs = new Map<string, Job>();

export function runningFor(cid: string): Job | undefined {
  for (const job of jobs.values()) if (job.char === cid && job.state === 'running') return job;
  return undefined;
}

export function get(id: string): Job | undefined {
  return jobs.get(id);
}

/** Starts the job, or returns the one already running on that character (and starts nothing). */
export function start(cid: string, action: string, kind: Job['kind'], args: string[]): { job: Job; started: boolean } {
  const busy = runningFor(cid);
  if (busy) return { job: busy, started: false };
  const job: Job = { id: randomUUID(), char: cid, action, kind, state: 'running', startedAt: new Date().toISOString() };
  jobs.set(job.id, job);
  const run = char(args);
  job.pid = run.child.pid;
  run.done.then((r: CliResult) => {
    job.result = r.json;
    job.stderrTail = r.stderrTail;
    job.state = r.json?.ok === true ? 'done' : 'failed';
    job.endedAt = new Date().toISOString();
  });
  return { job, started: true };
}

export function all(): Job[] {
  return [...jobs.values()];
}
