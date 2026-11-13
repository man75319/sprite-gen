// `sprite-gen char … --json` in a child process: the only way the Studio reads or changes a character
// (docs/char.md section 5). Arguments are built from whitelisted fields only; no shell.
import { spawn, type ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/** The chars root the CLI uses (ledger.chars_dir): SPRITE_GEN_CHARS_DIR, or work/chars in the checkout. */
export const CHARS_DIR = path.resolve(process.env.SPRITE_GEN_CHARS_DIR || path.join(REPO, 'work', 'chars'));

/** `--fake`: the real CLI with video-set swapped for studio/dev/fake_film.py's stand-in. Spends nothing. */
export const FAKE = process.argv.includes('--fake');

function command(): [string, string[]] {
  if (FAKE) return [path.join(REPO, '.venv', 'Scripts', 'python.exe'), [path.join(REPO, 'studio', 'dev', 'fake_film.py')]];
  return [path.join(REPO, '.venv', 'Scripts', 'sprite-gen.exe'), []];
}

/** ledger.NAME_RE: an id, an action name, a state. */
export const NAME_RE = /^[a-z0-9][a-z0-9-]{0,47}$/;

export class BadRequest extends Error {}

export function name(value: unknown, what: string): string {
  if (typeof value !== 'string' || !NAME_RE.test(value)) {
    throw new BadRequest(`${what} ${JSON.stringify(value)}: lowercase letters, digits and '-', at most 48`);
  }
  return value;
}

function text(value: unknown, what: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new BadRequest(`${what} is empty`);
  if (value.includes('\0')) throw new BadRequest(`${what}: NUL`);
  return value;
}

function number(value: unknown, what: string): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) throw new BadRequest(`${what} ${JSON.stringify(value)}: not a number`);
  return n;
}

/** Spec field → its `char new` / `char spec` flag, and how the value is checked. */
const SPEC_FLAGS: Record<string, [string, (v: unknown, what: string) => string]> = {
  provider: ['--provider', text],
  model: ['--model', text],
  resolution: ['--resolution', text],
  direction: ['--direction', text],
  facing: ['--facing', text],
  key: ['--key', text],
  body_plan: ['--body-plan', text],
  body_height: ['--body-height', (v, w) => String(Math.trunc(number(v, w)))],
  fps: ['--fps', (v, w) => String(number(v, w))],
  walk_start: ['--walk-start', text],
};

/**
 * `--flag=value`, one argv element: a value that starts with '-' cannot be taken for another flag.
 * Only the fields present (not undefined / null / '') become flags; `description` is one of them.
 */
export function specArgs(body: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const [field, [flag, check]] of Object.entries(SPEC_FLAGS)) {
    const v = body[field];
    if (v === undefined || v === null || v === '') continue;
    out.push(`${flag}=${check(v, field)}`);
  }
  if (body.description !== undefined && body.description !== null) {
    out.push(`--description=${text(body.description, 'description')}`);
  }
  return out;
}

export function motionArgs(body: Record<string, unknown>, required: boolean): string[] {
  const out: string[] = [];
  if (required || (body.motion !== undefined && body.motion !== null && body.motion !== '')) {
    out.push(`--motion=${text(body.motion, 'motion')}`);
  }
  const s = body.actionSeconds;
  if (s !== undefined && s !== null && s !== '') out.push(`--action-seconds=${number(s, 'actionSeconds')}`);
  if (body.dryRun) out.push('--dry-run');
  return out;
}

export interface CliResult {
  code: number | null;
  /** The one JSON object on stdout, or null when stdout was not that. */
  json: Record<string, unknown> | null;
  stderrTail: string;
}

export interface Running {
  child: ChildProcess;
  done: Promise<CliResult>;
}

const TAIL = 4000;

/** `sprite-gen char <args> --json`. */
export function char(args: string[]): Running {
  const [exe, pre] = command();
  const child = spawn(exe, [...pre, 'char', ...args, '--json'], {
    shell: false,
    windowsHide: true,
    cwd: REPO,
    env: { ...process.env, SPRITE_GEN_CHARS_DIR: CHARS_DIR, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const out: Buffer[] = [];
  let err = '';
  child.stdout!.on('data', (b: Buffer) => out.push(b));
  child.stderr!.on('data', (b: Buffer) => {
    err = (err + b.toString('utf8')).slice(-TAIL);
  });
  const done = new Promise<CliResult>((resolve) => {
    child.on('error', (e) => resolve({ code: null, json: null, stderrTail: `${err}\nspawn ${exe}: ${e.message}` }));
    child.on('close', (code) => {
      const stdout = Buffer.concat(out).toString('utf8').trim();
      let json: Record<string, unknown> | null = null;
      try {
        const parsed: unknown = JSON.parse(stdout);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) json = parsed as Record<string, unknown>;
      } catch {
        json = null;
      }
      resolve({ code, json, stderrTail: json ? err : `${err}\n--- stdout ---\n${stdout.slice(-TAIL)}` });
    });
  });
  return { child, done };
}
