// A paid run in progress: polls the job, finds the version it is filming (the one `char show` lists as running),
// and tails that version's video-set.log. Calls onEnd once the job (or, without a job, the character's lock) ends.
import { api, type Character, type Job } from './api.ts';
import { h } from './ui.ts';

const POLL_MS = 1500;

export interface Watch {
  el: HTMLElement;
  stop(): void;
}

export function watchJob(cid: string, jobId: string | null, onEnd: (job: Job | null) => void): Watch {
  const status = h('div', { class: 'job-status' }, '生成中…');
  const log = h('pre', { class: 'log' });
  const el = h('section', { class: 'job' }, h('h3', {}, '生成进度'), status, log);
  let stopped = false;
  let action: string | null = null;
  let version: number | null = null;
  let offset = 0;
  const t0 = Date.now();

  const findRunning = (c: Character): void => {
    for (const a of c.actions ?? []) {
      if (action && a.name !== action) continue;
      const v = a.versions.find((x) => x.status === 'running');
      if (v) {
        action = a.name;
        version = v.version;
        return;
      }
    }
  };

  const tick = async (): Promise<void> => {
    if (stopped) return;
    try {
      let job: Job | null = null;
      let ended = false;
      if (jobId) {
        job = (await api.job(jobId)).job;
        action = job.action;
        ended = job.state !== 'running';
      }
      if (version === null || !jobId) {
        const shown = await api.show(cid);
        findRunning(shown.character);
        if (!jobId) ended = !shown.character.busy;
      }
      if (action && version !== null) {
        const r = await api.log(cid, action, version, offset);
        if (r.text) {
          log.textContent = (log.textContent + r.text).slice(-20000);
          log.scrollTop = log.scrollHeight;
        }
        offset = r.offset;
      }
      const secs = Math.round((Date.now() - t0) / 1000);
      status.textContent = `${job ? `${job.kind} ${job.action}` : '后台作业（不是本页启动的）'}` +
        `${version !== null ? ` → v${version}` : ''}　已等 ${secs}s　${ended ? '已结束' : '进行中'}`;
      if (ended) {
        stopped = true;
        if (job?.state === 'failed') {
          el.append(h('pre', { class: 'error' }, String(job.result?.error ?? job.stderrTail ?? '失败')));
        }
        onEnd(job);
        return;
      }
    } catch (e) {
      status.textContent = `轮询出错：${e instanceof Error ? e.message : String(e)}（继续重试）`;
    }
    setTimeout(tick, POLL_MS);
  };
  void tick();
  return {
    el,
    stop() {
      stopped = true;
    },
  };
}

/** What a finished job comes to, for the page's notice (which outlives the re-render that follows the job). */
export function outcome(job: Job | null): { kind: 'ok' | 'error' | 'info'; node: Node } {
  if (!job) return { kind: 'info', node: h('span', {}, '后台作业已结束。') };
  const label = `${job.kind === 'add' ? '新建' : '重新生成'} ${job.action}`;
  const version = (job.result?.version as { version?: number } | undefined)?.version;
  if (job.state === 'done') return { kind: 'ok', node: h('span', {}, `${label} 完成${version ? `：v${version}，已设为当前` : ''}。`) };
  const error = String(job.result?.error ?? '') || job.stderrTail || '没有输出';
  return { kind: 'error', node: h('div', {}, h('div', {}, `${label} 失败${version ? `（v${version}）` : ''}：`), h('pre', {}, error)) };
}
