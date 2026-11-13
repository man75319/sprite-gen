// The Studio's HTTP server: REST over `sprite-gen char`, background jobs, and the chars dir as static files.
// Every read and write of a character goes through the CLI (server/cli.ts); /files only serves what is on disk.
import { createReadStream } from 'node:fs';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { BadRequest, CHARS_DIR, FAKE, char, motionArgs, name, specArgs } from './cli.ts';
import * as jobs from './jobs.ts';
import { SERVER_HOST, SERVER_PORT } from './ports.ts';

type Body = Record<string, unknown>;

class HttpError extends Error {
  constructor(readonly status: number, message: string, readonly extra: Body = {}) {
    super(message);
  }
}

function send(res: http.ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': data.length, 'Cache-Control': 'no-store' });
  res.end(data);
}

async function readBody(req: http.IncomingMessage, limit = 32 * 1024 * 1024): Promise<Buffer> {
  const parts: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new HttpError(413, 'body too large');
    parts.push(chunk as Buffer);
  }
  return Buffer.concat(parts);
}

async function json(req: http.IncomingMessage): Promise<Body> {
  const raw = (await readBody(req, 1024 * 1024)).toString('utf8');
  if (!raw.trim()) return {};
  try {
    const v: unknown = JSON.parse(raw);
    if (v && typeof v === 'object' && !Array.isArray(v)) return v as Body;
  } catch {
    // fall through
  }
  throw new BadRequest('body: not a JSON object');
}

/** A finished CLI call as a response: its JSON with 200, a refusal with 400 (409 when the character is busy),
 * and 500 with the stderr tail when stdout was not one JSON object. */
async function cli(res: http.ServerResponse, args: string[]): Promise<void> {
  const r = await char(args).done;
  if (!r.json) {
    send(res, 500, { ok: false, error: 'sprite-gen char did not print one JSON object', code: r.code, stderr: r.stderrTail });
    return;
  }
  if (r.json.ok === true) send(res, 200, r.json);
  else send(res, /is busy/.test(String(r.json.error)) ? 409 : 400, r.json);
}

/** A paid run: dry-run answers at once; the real one becomes a job (409 and nothing spawned while one runs). */
async function paid(res: http.ServerResponse, cid: string, action: string, kind: jobs.Job['kind'], args: string[], dryRun: boolean): Promise<void> {
  if (dryRun) return cli(res, args);
  const { job, started } = jobs.start(cid, action, kind, args);
  if (!started) throw new HttpError(409, `character ${cid} is busy: ${job.kind} ${job.action} (job ${job.id})`, { job });
  send(res, 202, { ok: true, jobId: job.id, job });
}

// ---- static files ---------------------------------------------------------------------------------------------

const TYPES: Record<string, string> = {
  '.png': 'image/png', '.json': 'application/json; charset=utf-8', '.mp4': 'video/mp4', '.gif': 'image/gif',
  '.webp': 'image/webp', '.log': 'text/plain; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8',
};

/** The file under CHARS_DIR a /files path names, or a 403/404. Resolved, then followed through links, and both must
 * stay inside the root. */
async function resolveInRoot(rel: string): Promise<string> {
  let decoded: string;
  try {
    decoded = decodeURIComponent(rel);
  } catch {
    throw new HttpError(400, 'bad path');
  }
  if (decoded.includes('\0')) throw new HttpError(400, 'bad path');
  const root = path.resolve(CHARS_DIR);
  const inside = (p: string) => p.toLowerCase().startsWith((root + path.sep).toLowerCase());
  const full = path.resolve(root, decoded);
  if (!inside(full)) throw new HttpError(403, 'outside the chars dir');
  let real: string;
  try {
    real = await fs.realpath(full);
  } catch {
    throw new HttpError(404, 'not found');
  }
  if (!inside(real)) throw new HttpError(403, 'outside the chars dir');
  return real;
}

async function serveFile(req: http.IncomingMessage, res: http.ServerResponse, rel: string): Promise<void> {
  const file = await resolveInRoot(rel);
  const stat = await fs.stat(file);
  if (!stat.isFile()) throw new HttpError(404, 'not found');
  const ext = path.extname(file).toLowerCase();
  const headers: http.OutgoingHttpHeaders = {
    'Content-Type': TYPES[ext] ?? 'application/octet-stream',
    'Accept-Ranges': 'bytes',
    // json changes in place (version.json while running, action.json on use); the rest is one file per version,
    // but build/ is rewritten, so nothing is cached
    'Cache-Control': 'no-store',
  };
  const range = req.headers.range && /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
  if (range && (range[1] || range[2])) {
    let start = range[1] ? Number(range[1]) : stat.size - Number(range[2]);
    let end = range[1] && range[2] ? Number(range[2]) : stat.size - 1;
    start = Math.max(0, start);
    end = Math.min(end, stat.size - 1);
    if (start > end || start >= stat.size) {
      res.writeHead(416, { 'Content-Range': `bytes */${stat.size}` });
      res.end();
      return;
    }
    res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1 });
    if (req.method === 'HEAD') return void res.end();
    createReadStream(file, { start, end }).pipe(res);
    return;
  }
  res.writeHead(200, { ...headers, 'Content-Length': stat.size });
  if (req.method === 'HEAD') return void res.end();
  createReadStream(file).pipe(res);
}

/** The new bytes of a version's video-set.log from a byte offset (the page polls it while the job runs). */
async function logTail(res: http.ServerResponse, cid: string, action: string, n: number, offset: number): Promise<void> {
  const file = await resolveInRoot(`${cid}/actions/${action}/v${n}/video-set.log`);
  const handle = await fs.open(file, 'r');
  try {
    const { size } = await handle.stat();
    const from = Math.min(Math.max(0, offset), size);
    const len = Math.min(size - from, 256 * 1024);
    const buf = Buffer.alloc(len);
    await handle.read(buf, 0, len, from);
    send(res, 200, { ok: true, offset: from + len, size, text: buf.toString('utf8') });
  } finally {
    await handle.close();
  }
}

// ---- upload ---------------------------------------------------------------------------------------------------

/** POST /api/chars: multipart (ref file + fields) → a temp file → `char new --ref`. */
async function createChar(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const raw = await readBody(req);
  const form = await new Request('http://studio/upload', {
    method: 'POST',
    headers: { 'content-type': String(req.headers['content-type'] ?? '') },
    body: new Uint8Array(raw),
  }).formData().catch(() => {
    throw new BadRequest('body: expected multipart/form-data');
  });
  const ref = form.get('ref');
  if (!(ref instanceof Blob) || ref.size === 0) throw new BadRequest('ref: no image');
  const fields: Body = {};
  for (const [k, v] of form.entries()) if (k !== 'ref' && typeof v === 'string') fields[k] = v;
  const cid = name(fields.id, 'character id');
  if (!fields.body_height) throw new BadRequest('body_height is required');
  const args = ['new', cid, ...specArgs({ ...fields, description: fields.description ?? '' })];
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'studio-ref-'));
  try {
    const file = path.join(dir, 'ref.png');
    await fs.writeFile(file, Buffer.from(await ref.arrayBuffer()));
    await cli(res, [...args.slice(0, 2), `--ref=${file}`, ...args.slice(2)]);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

// ---- routes ---------------------------------------------------------------------------------------------------

type Handler = (req: http.IncomingMessage, res: http.ServerResponse, m: string[], url: URL) => Promise<void>;
const routes: [string, RegExp, Handler][] = [];
const route = (method: string, pattern: RegExp, h: Handler) => routes.push([method, pattern, h]);
const SEG = '([^/]+)';

route('GET', /^\/api\/chars$/, async (_q, res) => cli(res, ['list']));

route('POST', /^\/api\/chars$/, createChar);

route('GET', new RegExp(`^/api/chars/${SEG}$`), async (_q, res, [id]) => {
  const cid = name(id, 'character id');
  const r = await char(['show', cid]).done;
  if (!r.json) return send(res, 500, { ok: false, error: 'sprite-gen char did not print one JSON object', stderr: r.stderrTail });
  // the job this server runs on it, so a reloaded page picks the progress up again
  send(res, r.json.ok ? 200 : 404, { ...r.json, job: jobs.runningFor(cid) ?? null });
});

route('PATCH', new RegExp(`^/api/chars/${SEG}/spec$`), async (req, res, [id]) => {
  const body = await json(req);
  const args = specArgs(body);
  if (!args.length) throw new BadRequest('nothing to change');
  await cli(res, ['spec', name(id, 'character id'), ...args]);
});

route('POST', new RegExp(`^/api/chars/${SEG}/actions$`), async (req, res, [id]) => {
  const body = await json(req);
  const cid = name(id, 'character id');
  const action = name(body.name, 'action name');
  const args = ['action', 'add', cid, action, `--state=${name(body.state, 'state')}`, ...motionArgs(body, true)];
  await paid(res, cid, action, 'add', args, Boolean(body.dryRun));
});

route('POST', new RegExp(`^/api/chars/${SEG}/actions/${SEG}/regen$`), async (req, res, [id, act]) => {
  const body = await json(req);
  const cid = name(id, 'character id');
  const action = name(act, 'action name');
  await paid(res, cid, action, 'regen', ['action', 'regen', cid, action, ...motionArgs(body, false)], Boolean(body.dryRun));
});

route('POST', new RegExp(`^/api/chars/${SEG}/actions/${SEG}/use$`), async (req, res, [id, act]) => {
  const body = await json(req);
  const n = Number(body.version);
  if (!Number.isInteger(n) || n < 1) throw new BadRequest('version: a whole number from 1');
  await cli(res, ['action', 'use', name(id, 'character id'), name(act, 'action name'), String(n)]);
});

route('DELETE', new RegExp(`^/api/chars/${SEG}/actions/${SEG}$`), async (_q, res, [id, act], url) => {
  const purge = url.searchParams.get('purge') === '1';
  await cli(res, ['action', 'rm', name(id, 'character id'), name(act, 'action name'), ...(purge ? ['--purge'] : [])]);
});

route('POST', new RegExp(`^/api/chars/${SEG}/build$`), async (_q, res, [id]) => cli(res, ['build', name(id, 'character id')]));

route('GET', new RegExp(`^/api/jobs/${SEG}$`), async (_q, res, [jobId]) => {
  const job = jobs.get(jobId);
  if (!job) throw new HttpError(404, `no job ${jobId}`);
  send(res, 200, { ok: true, job });
});

route('GET', new RegExp(`^/api/chars/${SEG}/actions/${SEG}/versions/(\\d+)/log$`), async (_q, res, [id, act, n], url) =>
  logTail(res, name(id, 'character id'), name(act, 'action name'), Number(n), Number(url.searchParams.get('offset') ?? 0) || 0));

route('GET', /^\/api\/server$/, async (_q, res) => send(res, 200, { ok: true, charsDir: CHARS_DIR, fake: FAKE }));

const server = http.createServer(async (req, res) => {
  try {
    // the raw path, not URL-normalised: /files/../../.env must reach the check below as it was sent
    const rawPath = (req.url ?? '/').split('?')[0];
    const url = new URL(req.url ?? '/', 'http://studio');
    if ((req.method === 'GET' || req.method === 'HEAD') && rawPath.startsWith('/files/')) {
      return await serveFile(req, res, rawPath.slice('/files/'.length));
    }
    for (const [method, pattern, handler] of routes) {
      const m = pattern.exec(rawPath);
      if (m && method === req.method) return await handler(req, res, m.slice(1).map(decodeURIComponent), url);
    }
    throw new HttpError(404, `no route ${req.method} ${rawPath}`);
  } catch (e) {
    if (res.headersSent) return void res.destroy();
    if (e instanceof HttpError) return send(res, e.status, { ok: false, error: e.message, ...e.extra });
    if (e instanceof BadRequest) return send(res, 400, { ok: false, error: e.message });
    console.error(e);
    send(res, 500, { ok: false, error: e instanceof Error ? e.message : String(e) });
  }
});

server.listen(SERVER_PORT, SERVER_HOST, () => {
  console.log(`studio server http://${SERVER_HOST}:${SERVER_PORT}  chars ${CHARS_DIR}${FAKE ? '  (FAKE: video-set is studio/dev/fake_film.py, nothing is spent)' : ''}`);
});
