// 角色详情：规格（可改）、动作列表、新建动作、Build、「全部动作」舞台。
import { api, fileUrl, type Character, type Job } from '../api.ts';
import { zip } from '../zip.ts';
import { outcome, watchJob, type Watch } from '../job.ts';
import { showGallery, type Badge, type GalleryItem } from '../gallery.ts';
import * as preview from '../preview.ts';
import { LABELS, specFields } from '../specForm.ts';
import { NAME_PATTERN, confirmSpend, errorBox, flash, formValues, go, h } from '../ui.ts';

export async function characterPage(root: HTMLElement, cid: string): Promise<() => void> {
  let watch: Watch | null = null;
  let alive = true;
  const notice = h('div'); // survives re-renders: the result of the last action taken here
  // the job this page just started: watched even if it is over before the page re-renders (a fast failure)
  let started: string | null = null;

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
    const busy = Boolean(job || c.busy);

    // ---- header ----
    const header = h('header', { class: 'char-head' },
      still(c.ref, '原图', '用户给的原图（原样）'),
      c.cutout ? still(c.cutout, '透明背景', '抠掉背景的角色，之后的步骤都从它出发') : null,
      still(c.base, '绿幕底图', '透明背景图铺在 key 色上，video-set 拍的就是它'),
      h('div', {},
        h('h1', {}, c.id, busy ? h('span', { class: 'badge busy' }, '生成中') : null),
        h('p', {}, c.description),
        h('small', { class: 'muted' }, `spec r${c.spec_rev}`)));

    const jobSlot = h('div');
    if (busy || started) {
      watch = watchJob(cid, job?.id ?? started, (ended) => {
        if (!alive) return;
        started = null;
        const o = outcome(ended);
        flash(notice, o.node, o.kind);
        void render();
      });
      jobSlot.append(watch.el);
    }

    // ---- spec ----
    const specMsg = h('div');
    const specForm = h('form', { class: 'grid-form' },
      h('label', { class: 'wide' }, h('span', {}, '描述词（改它不算改规格）'), h('input', { name: 'description', value: c.description, required: true })),
      ...specFields(c.spec, { lockKey: true }),
      h('div', { class: 'wide' }, h('button', { type: 'submit', disabled: busy }, '保存规格')));
    specForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = formValues(specForm);
      const body: Record<string, unknown> = {};
      for (const field of Object.keys(LABELS)) {
        if (field === 'key') continue;
        const now = (c.spec as unknown as Record<string, unknown>)[field];
        const got = v[field];
        if (field === 'fps') {
          const want = got === undefined ? null : Number(got) > 0 ? Number(got) : null;
          if (want !== now) body.fps = want === null ? 0 : want; // 0: back to "as filmed"
          continue;
        }
        if (got !== undefined && String(now) !== got) body[field] = field === 'body_height' ? Number(got) : got;
      }
      if (v.description && v.description !== c.description) body.description = v.description;
      if (!Object.keys(body).length) return flash(specMsg, '没有改动', 'info');
      try {
        const r = await api.spec(cid, body);
        const after = await api.show(cid);
        const stale = (after.character.actions ?? []).filter((a) => a.current_stale).length;
        flash(notice, r.changed.length
          ? `规格已改：${r.changed.join(', ')}（spec r${after.character.spec_rev}）。` + (stale
            ? `已有 ${stale} 个动作变成旧规格，build 会跳过它们，需重新生成。`
            : '现有动作的当前版都与新规格一致。')
          : '描述词已改（不算改规格）；之后生成的新版用新描述词。', stale ? 'error' : 'ok');
        await render();
      } catch (err) {
        specMsg.replaceChildren(errorBox(err));
      }
    });

    // ---- actions: a grid that plays each action's current version; a click opens it ----
    const actionHash = (name: string) => `#/c/${encodeURIComponent(cid)}/a/${encodeURIComponent(name)}`;
    const items: GalleryItem[] = (c.actions ?? []).map((a) => {
      const cur = a.versions.find((v) => v.version === a.current);
      const last = a.versions[a.versions.length - 1];
      const badges: Badge[] = [];
      if (a.current_stale) badges.push({ text: '旧规格', color: 0xf59e0b });
      if (cur?.prompt_match === false) badges.push({ text: '提示词不一致', color: 0xf87171 });
      if (last?.status === 'running') badges.push({ text: `v${last.version} 生成中`, color: 0xfacc15 });
      else if (last && last.version !== a.current && last.status === 'failed') badges.push({ text: `v${last.version} 失败`, color: 0xf87171 });
      const f = cur?.ok ? cur.files : null;
      const files = f?.png && f.json && f['meta.json'] ? { png: f.png, json: f.json, meta: f['meta.json'] } : null;
      return { name: a.name, version: a.current, files, badges,
        note: last?.status === 'running' && !a.current ? '第一版生成中…' : undefined };
    });
    const gallerySlot = h('div', { class: 'gallery' });

    // ---- new action ----
    const addMsg = h('div');
    const addForm = h('form', { class: 'grid-form' },
      h('label', {}, h('span', {}, '名字'), h('input', { name: 'name', required: true, pattern: NAME_PATTERN })),
      h('label', {}, h('span', {}, 'state（提示词骨架）'), h('input', { name: 'state', required: true, list: 'states', pattern: NAME_PATTERN })),
      h('datalist', { id: 'states' }, ...['idle', 'walk', 'run', 'attack', 'hurt', 'death', 'jump'].map((s) => h('option', { value: s }))),
      h('label', {}, h('span', {}, 'action seconds（可空）'), h('input', { name: 'actionSeconds', type: 'number', min: 0, step: 'any' })),
      h('label', { class: 'wide' }, h('span', {}, 'motion（用自己的话写动作）'), h('textarea', { name: 'motion', required: true, rows: 3 })),
      h('div', { class: 'wide' }, h('button', { type: 'submit', class: 'primary', disabled: busy }, '预览提示词 → 生成')));
    addForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = formValues(addForm);
      try {
        const dry = await api.addDry(cid, body);
        if (!(await confirmSpend(`${cid}/${body.name}`, dry.prompt, dry.argv))) return;
        started = (await api.add(cid, body)).jobId;
        flash(notice, `已开始生成 ${body.name}`, 'info');
        await render();
      } catch (err) {
        addMsg.replaceChildren(errorBox(err));
      }
    });

    // ---- build + stage ----
    // the build's files zipped as they are on disk (same names: <id>.json names <id>.png as its image)
    const downloads = h('div', { class: 'downloads' });
    // export-phaser's alignment check: every action's feet against one line (not shipped, only looked at)
    const align = h('div');
    if (c.build) {
      const base = c.build.replace(/\.meta\.json$/, '');
      const files = [`${base}.png`, `${base}.json`, `${base}.meta.json`];
      const alignSrc = fileUrl(`${base}.align.png`, true);
      align.append(h('h3', {}, '脚底对齐检查', h('small', { class: 'muted' }, `  ${base}.align.png · 点图看原尺寸`)),
        h('a', { href: alignSrc, target: '_blank', rel: 'noopener', class: 'sheet-wrap' }, h('img', { src: alignSrc, alt: 'align', class: 'sheet' })));
      const zipBtn = h('button', { type: 'button', class: 'primary' }, `打包下载 ${cid}-atlas.zip`);
      zipBtn.addEventListener('click', async () => {
        zipBtn.disabled = true;
        try {
          const got = await Promise.all(files.map(async (rel) => {
            const res = await fetch(fileUrl(rel, true));
            if (!res.ok) throw new Error(`${rel}: HTTP ${res.status}`);
            return { name: rel.split('/').pop()!, data: new Uint8Array(await res.arrayBuffer()) };
          }));
          const a = h('a', { href: URL.createObjectURL(zip(got)), download: `${cid}-atlas.zip` });
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
        } catch (err) {
          buildMsg.replaceChildren(errorBox(err));
        } finally {
          zipBtn.disabled = false;
        }
      });
      const names = files.map((rel) => rel.split('/').pop()!).join('、');
      downloads.append(zipBtn,
        h('div', { class: 'muted small' }, `包内：${names}。load.atlas 吃 .png + .json，.meta.json 是动画清单（帧名前缀、帧数、帧率、锚点）。`));
    }
    const buildMsg = h('div');
    const stageSlot = h('div', { class: 'stage' });
    const animButtons = h('div', { class: 'anim-buttons' });
    const buildBtn = h('button', { type: 'button', class: 'primary', disabled: busy }, 'Build（当前版合成一张图集）');
    buildBtn.addEventListener('click', async () => {
      buildBtn.disabled = true;
      try {
        const r = await api.build(cid);
        flash(notice, h('div', {},
          h('div', {}, `已 build：${r.built.join(', ')}`),
          ...r.skipped.map((s) => h('div', { class: 'warn' }, `跳过 ${s.action}：${s.why}`)),
          ...(r.warnings ?? []).map((w) => h('div', { class: 'warn' }, w))), 'ok');
        await render();
      } catch (err) {
        buildMsg.replaceChildren(errorBox(err));
        buildBtn.disabled = false;
      }
    });

    root.replaceChildren(
      h('a', { href: '#/' }, '← 角色'),
      header,
      notice,
      jobSlot,
      h('section', { class: 'panel' },
        h('h2', {}, '动作', h('small', { class: 'muted' }, '  每格播当前版，点开看详情 / 历史版本 / 重新生成 / 删除')),
        items.length ? gallerySlot : h('p', { class: 'muted' }, '还没有动作。'),
        h('details', { class: 'add-action', open: !items.length },
          h('summary', {}, '＋ 新建动作'), addMsg, addForm)),
      h('section', { class: 'panel' }, h('h2', {}, '全部动作（build 产物）'), buildBtn, buildMsg, downloads, animButtons, stageSlot, align),
      h('details', { class: 'panel' }, h('summary', {}, h('h2', {}, '规格', h('small', { class: 'muted' }, `  spec r${c.spec_rev}`))), specMsg, specForm),
    );

    if (items.length) await showGallery(gallerySlot, items, (name) => go(actionHash(name)));

    if (c.build) {
      const base = c.build.replace(/\.meta\.json$/, '');
      try {
        const shown = await preview.show(stageSlot, { png: `${base}.png`, json: `${base}.json`, meta: c.build });
        if (shown && alive) {
          animButtons.replaceChildren(...shown.anims.map((k) => h('button', { type: 'button', onclick: () => preview.play(k) }, k)));
        }
      } catch (err) {
        stageSlot.replaceChildren(errorBox(err));
      }
    } else {
      stageSlot.replaceChildren(h('p', { class: 'muted' }, '还没有 build。'));
    }
  };

  await render();
  return () => {
    alive = false;
    watch?.stop();
  };
}

/** One of the character's stills, labelled; the full file opens on click. */
function still(file: string, label: string, title: string): HTMLElement {
  const src = fileUrl(file);
  return h('figure', { class: 'still', title: `${file.split('/').pop()}：${title}` },
    h('a', { href: src, target: '_blank', rel: 'noopener' }, h('img', { src, alt: label })),
    h('figcaption', {}, label));
}
