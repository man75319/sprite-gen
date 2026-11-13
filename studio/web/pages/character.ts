// 角色详情：规格（可改）、动作列表、新建动作、Build、「全部动作」舞台。
import { api, fileUrl, type Character, type Job } from '../api.ts';
import { watchJob, type Watch } from '../job.ts';
import * as preview from '../preview.ts';
import { LABELS, specFields } from '../specForm.ts';
import { NAME_PATTERN, confirmDialog, confirmSpend, errorBox, flash, formValues, h } from '../ui.ts';

export async function characterPage(root: HTMLElement, cid: string): Promise<() => void> {
  let watch: Watch | null = null;
  let alive = true;
  const notice = h('div'); // survives re-renders: the result of the last action taken here

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
      h('img', { src: fileUrl(c.ref), alt: 'ref', title: 'ref.png（原样）' }),
      h('img', { src: fileUrl(c.base), alt: 'base', title: 'base.png（铺在 key 上，video-set 拍的就是它）' }),
      h('div', {},
        h('h1', {}, c.id, busy ? h('span', { class: 'badge busy' }, '生成中') : null),
        h('p', {}, c.description),
        h('small', { class: 'muted' }, `spec r${c.spec_rev}`)));

    const jobSlot = h('div');
    if (busy) {
      watch = watchJob(cid, job?.id ?? null, () => {
        if (alive) void render();
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

    // ---- actions ----
    const rows = (c.actions ?? []).map((a) => {
      const cur = a.versions.find((v) => v.version === a.current);
      const last = a.versions[a.versions.length - 1];
      const rm = async (purge: boolean) => {
        const ok = await confirmDialog(purge ? '彻底删除' : '删除动作',
          h('p', {}, purge ? `物理删除 ${a.name} 的所有版本，不可恢复。` : `把 ${a.name} 整个移进 ${cid}/_trash/。`), purge ? '彻底删除' : '移进回收站', true);
        if (!ok) return;
        try {
          const r = await api.rm(cid, a.name, purge);
          flash(notice, r.purged ? `已彻底删除 ${r.removed}` : `已移进 ${r.trash}`, 'ok');
          await render();
        } catch (err) {
          notice.replaceChildren(errorBox(err));
        }
      };
      return h('tr', {},
        h('td', {}, h('a', { href: `#/c/${encodeURIComponent(cid)}/a/${encodeURIComponent(a.name)}` }, a.name)),
        h('td', {}, a.state),
        h('td', { class: 'motion' }, a.motion),
        h('td', {}, a.current ? `v${a.current}` : '—', a.current_stale ? h('span', { class: 'badge stale' }, '旧规格') : null,
          cur && cur.prompt_match === false ? h('span', { class: 'badge bad' }, '提示词不一致') : null),
        h('td', {}, `${a.versions.length} 版`, last ? h('span', { class: `badge ${last.status}` }, `v${last.version} ${last.status}`) : null),
        h('td', {},
          h('button', { type: 'button', disabled: busy, onclick: () => rm(false) }, '删除'),
          h('button', { type: 'button', class: 'danger', disabled: busy, onclick: () => rm(true) }, '彻底删除')));
    });
    const table = rows.length
      ? h('table', {}, h('thead', {}, h('tr', {}, ...['动作', 'state', 'motion', '当前', '版本', ''].map((t) => h('th', {}, t)))), h('tbody', {}, ...rows))
      : h('p', { class: 'muted' }, '还没有动作。');

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
        await api.add(cid, body);
        flash(notice, `已开始生成 ${body.name}`, 'info');
        await render();
      } catch (err) {
        addMsg.replaceChildren(errorBox(err));
      }
    });

    // ---- build + stage ----
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
      h('section', { class: 'panel' }, h('h2', {}, '动作'), table),
      h('section', { class: 'panel' }, h('h2', {}, '新建动作'), addMsg, addForm),
      h('section', { class: 'panel' }, h('h2', {}, '全部动作（build 产物）'), buildBtn, buildMsg, animButtons, stageSlot),
      h('section', { class: 'panel' }, h('h2', {}, '规格'), specMsg, specForm),
    );

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

