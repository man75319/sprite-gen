// 动作详情（docs/char.md section 4）：任一版本的三样 —— 提示词 / 原始视频 / 最终效果 —— 加历史版本。
// 切版本时三样一起切；「设为当前」走 `char action use`。
import { api, fileJson, fileUrl, type Action, type Character, type Job, type Version } from '../api.ts';
import { outcome, watchJob, type Watch } from '../job.ts';
import * as preview from '../preview.ts';
import { confirmDialog, confirmSpend, errorBox, flash, fmtTime, formValues, go, h } from '../ui.ts';

const FRAME = 1 / 24; // Seedance films at 24

interface VersionJson {
  planned_prompt?: string;
  motion?: string;
  argv?: string[];
  spec_rev?: number;
}

export async function actionPage(root: HTMLElement, cid: string, name: string, wanted: number | null): Promise<() => void> {
  let watch: Watch | null = null;
  let alive = true;
  const notice = h('div');
  // the job this page just started: watched even if it is over before the page re-renders (a fast failure)
  let started: string | null = null;
  const hashFor = (n: number) => `#/c/${encodeURIComponent(cid)}/a/${encodeURIComponent(name)}/v/${n}`;

  const render = async (): Promise<void> => {
    watch?.stop();
    watch = null;
    let c: Character;
    let job: Job | null;
    try {
      ({ character: c, job } = await api.show(cid));
    } catch (e) {
      root.replaceChildren(h('a', { href: '#/' }, '← 角色'), errorBox(e));
      return;
    }
    if (!alive) return;
    const a = c.actions?.find((x) => x.name === name);
    const back = h('a', { href: `#/c/${encodeURIComponent(cid)}` }, `← ${cid}`);
    if (!a) {
      root.replaceChildren(back, errorBox(`${cid} 没有动作 ${name}（可能已删除）`));
      return;
    }
    const busy = Boolean(job || c.busy);
    const shownN = wanted && a.versions.some((v) => v.version === wanted) ? wanted : a.current ?? a.versions[a.versions.length - 1]?.version ?? null;
    const v = a.versions.find((x) => x.version === shownN) ?? null;

    const jobSlot = h('div');
    if (busy || started) {
      watch = watchJob(cid, job?.id ?? started, (ended) => {
        if (!alive) return;
        started = null;
        const o = outcome(ended);
        flash(notice, o.node, o.kind);
        wanted = null; // the new version becomes current when it succeeds; show that
        void render();
      });
      jobSlot.append(watch.el);
    }

    const list = h('ul', { class: 'versions' }, ...[...a.versions].reverse().map((x) => h('li', {},
      h('a', { href: hashFor(x.version), class: x.version === shownN ? 'selected' : '' },
        h('strong', {}, `v${x.version}`),
        h('span', { class: `badge ${x.status}` }, x.status),
        x.version === a.current ? h('span', { class: 'badge current' }, '当前') : null,
        x.stale ? h('span', { class: 'badge stale' }, '旧规格') : null,
        x.prompt_match === false ? h('span', { class: 'badge bad' }, '提示词不一致') : null,
        h('small', {}, fmtTime(x.started))))));

    const rm = async (purge: boolean) => {
      const ok = await confirmDialog(purge ? '彻底删除' : '删除动作',
        h('p', {}, purge ? `物理删除 ${name} 的所有版本，不可恢复。` : `把 ${name} 整个移进 ${cid}/_trash/（可以手工找回）。`),
        purge ? '彻底删除' : '移进回收站', true);
      if (!ok) return;
      try {
        await api.rm(cid, name, purge);
        go(`#/c/${encodeURIComponent(cid)}`);
      } catch (err) {
        notice.replaceChildren(errorBox(err));
      }
    };
    const about = h('section', { class: 'panel action-about' },
      h('dl', {},
        h('dt', {}, 'state'), h('dd', {}, h('code', {}, a.state), h('span', { class: 'muted' }, '  决定提示词骨架、播放方式、切循环的方式')),
        h('dt', {}, 'motion'), h('dd', {}, a.motion, h('small', { class: 'muted' }, '  （下一版默认用它；每一版自己用的写在下面）')),
        h('dt', {}, 'action seconds'), h('dd', {}, a.action_seconds != null ? String(a.action_seconds) : '—')),
      h('div', {},
        h('button', { type: 'button', disabled: busy, onclick: () => rm(false) }, '删除动作'),
        h('button', { type: 'button', class: 'danger', disabled: busy, onclick: () => rm(true) }, '彻底删除')));

    root.replaceChildren(
      back,
      h('h1', {}, `${cid} / ${name}`),
      notice,
      jobSlot,
      about,
      h('div', { class: 'action-layout' },
        h('aside', {}, h('h2', {}, '历史版本'), list, regenForm(c, a, busy)),
        h('div', { class: 'version-detail' }, v ? versionDetail(a, v, busy) : h('p', { class: 'muted' }, '还没有版本。'))),
    );
  };

  function regenForm(c: Character, a: Action, busy: boolean): HTMLElement {
    const msg = h('div');
    const form = h('form', { class: 'stack-form' },
      h('label', {}, h('span', {}, 'motion（留空 = 沿用）'), h('textarea', { name: 'motion', rows: 4 }, a.motion)),
      h('label', {}, h('span', {}, 'action seconds（留空 = 沿用）'),
        h('input', { name: 'actionSeconds', type: 'number', min: 0, step: 'any', value: a.action_seconds ?? '' })),
      h('button', { type: 'submit', class: 'primary', disabled: busy }, '预览提示词 → 重新生成'));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = formValues(form);
      try {
        const dry = await api.regenDry(c.id, a.name, body);
        if (!(await confirmSpend(`${c.id}/${a.name} 新一版`, dry.prompt, dry.argv))) return;
        started = (await api.regen(c.id, a.name, body)).jobId;
        flash(notice, '已开始重新生成；旧版保留。', 'info');
        await render();
      } catch (err) {
        msg.replaceChildren(errorBox(err));
      }
    });
    return h('section', { class: 'panel' }, h('h3', {}, '重新生成'), msg, form);
  }

  function versionDetail(a: Action, v: Version, busy: boolean): HTMLElement {
    const vdir = `${cid}/actions/${name}/v${v.version}`;
    const files = v.files ?? {};

    const useBtn = h('button', { type: 'button', class: 'primary', disabled: busy || !v.ok || v.version === a.current }, '设为当前');
    useBtn.addEventListener('click', async () => {
      try {
        await api.use(cid, name, v.version);
        flash(notice, `v${v.version} 已设为当前`, 'ok');
        await render();
      } catch (err) {
        notice.replaceChildren(errorBox(err));
      }
    });
    const info = h('div', { class: 'version-info' },
      h('div', {}, useBtn,
        v.version === a.current ? h('span', { class: 'badge current' }, '当前') : null,
        v.stale ? h('span', { class: 'badge stale' }, '旧规格（build 会跳过）') : null),
      h('dl', {},
        h('dt', {}, '状态'), h('dd', {}, v.status),
        h('dt', {}, '开始'), h('dd', {}, fmtTime(v.started)),
        h('dt', {}, '耗时'), h('dd', {}, v.elapsed_seconds != null ? `${v.elapsed_seconds}s` : '—'),
        h('dt', {}, 'seam'), h('dd', {}, v.seam != null ? String(v.seam) : '—'),
        h('dt', {}, 'action seconds'), h('dd', {}, v.action_seconds != null ? String(v.action_seconds) : '—')),
      v.error ? h('pre', { class: 'error' }, v.error) : null,
      ...(v.warnings ?? []).map((w) => h('div', { class: 'warn' }, w)));

    // 1. prompts
    const sent = h('pre', { class: 'prompt' }, '加载中…');
    const planned = h('pre', { class: 'prompt' }, '加载中…');
    const match = v.prompt_match;
    const prompts = h('section', { class: `panel prompts ${match === false ? 'mismatch' : ''}` },
      h('h3', {}, '1. 提示词',
        match === true ? h('span', { class: 'badge ok' }, 'prompt_match ✓') : match === false ? h('span', { class: 'badge bad' }, 'prompt_match ✗ 与计划不一致') : null),
      h('div', { class: 'cols' },
        h('div', {}, h('h4', {}, '你写的 motion'), h('pre', { class: 'prompt' }, v.motion ?? '')),
        h('div', {}, h('h4', {}, '实际发送（clip.report.json#prompt）'), sent)),
      h('details', {}, h('summary', {}, '计划提示词（version.json#planned_prompt）'), planned));
    void (async () => {
      const report = files.clip_report ? await fileJson<{ prompt?: string }>(files.clip_report) : await fileJson<{ prompt?: string }>(`${vdir}/side-${a.state}/clip.report.json`);
      sent.textContent = report?.prompt ?? '（还没有 clip.report.json）';
      const vj = await fileJson<VersionJson>(`${vdir}/version.json`);
      planned.textContent = vj?.planned_prompt ?? '（无）';
    })();

    // 2. clip
    const clipRel = files.clip ?? `${vdir}/side-${a.state}/clip.mp4`;
    const video = h('video', { src: fileUrl(clipRel), controls: true, preload: 'auto', muted: true, loop: true, playsInline: true });
    const seek = h('input', { type: 'range', min: 0, max: 1000, value: 0, class: 'seek' });
    const time = h('span', { class: 'mono' }, '0.000s');
    const step = (d: number) => {
      video.pause();
      video.currentTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + d));
    };
    const sync = () => {
      const f = Math.round(video.currentTime / FRAME);
      time.textContent = `${video.currentTime.toFixed(3)}s  帧 ${f}${video.duration ? ` / ${Math.round(video.duration / FRAME)}` : ''}`;
      if (video.duration && document.activeElement !== seek) seek.value = String(Math.round((video.currentTime / video.duration) * 1000));
    };
    video.addEventListener('timeupdate', sync);
    video.addEventListener('seeked', sync);
    video.addEventListener('loadedmetadata', sync);
    video.addEventListener('error', () => videoBox.append(h('div', { class: 'warn' }, `视频加载失败：${clipRel}`)));
    seek.addEventListener('input', () => {
      if (video.duration) {
        video.pause();
        video.currentTime = (Number(seek.value) / 1000) * video.duration;
      }
    });
    const videoBox = h('section', { class: 'panel' },
      h('h3', {}, '2. 原始视频', h('small', { class: 'muted' }, `  ${clipRel}`)),
      video,
      h('div', { class: 'video-controls' },
        h('button', { type: 'button', onclick: () => step(-FRAME) }, '◀ 上一帧'),
        h('button', { type: 'button', onclick: () => (video.paused ? void video.play() : video.pause()) }, '播放/暂停'),
        h('button', { type: 'button', onclick: () => step(FRAME) }, '下一帧 ▶'),
        time),
      seek);

    // 3. Phaser
    const stage = h('div', { class: 'stage' });
    const buttons = h('div', { class: 'anim-buttons' });
    const result = h('section', { class: 'panel' },
      h('h3', {}, '3. 最终效果（Phaser 4，', h('code', {}, `${vdir}/preview/`), '）'), buttons, stage,
      h('small', { class: 'muted' }, '红线 = 脚底线（meta.anchor 所在行），青色十字 = 锚点，灰框 = cell'));
    if (files.png && files.json && files['meta.json']) {
      preview.show(stage, { png: files.png, json: files.json, meta: files['meta.json'] }).then((shown) => {
        if (shown) buttons.replaceChildren(...shown.anims.map((k) => h('button', { type: 'button', onclick: () => preview.play(k) }, k)));
      }, (err) => stage.replaceChildren(errorBox(err)));
    } else {
      stage.replaceChildren(h('p', { class: 'muted' }, v.status === 'running' ? '生成中…' : '这一版没有 preview 图集。'));
    }

    return h('div', {}, h('h2', {}, `v${v.version}`), info, prompts, videoBox, result);
  }

  await render();
  return () => {
    alive = false;
    watch?.stop();
  };
}

