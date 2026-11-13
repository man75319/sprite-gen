// 角色列表：卡片 + 新建角色。
import { api, fileUrl } from '../api.ts';
import { DEFAULTS, specFields } from '../specForm.ts';
import { NAME_PATTERN, errorBox, flash, formValues, go, h } from '../ui.ts';

export async function listPage(root: HTMLElement): Promise<void> {
  root.replaceChildren(h('h1', {}, '角色'), h('p', {}, '加载中…'));
  let chars;
  try {
    chars = (await api.list()).characters;
  } catch (e) {
    root.replaceChildren(h('h1', {}, '角色'), errorBox(e));
    return;
  }
  const cards = h('div', { class: 'cards' },
    ...chars.map((c) => h('a', { class: 'card', href: `#/c/${encodeURIComponent(c.id)}` },
      h('img', { src: fileUrl(c.ref), alt: c.id, loading: 'lazy' }),
      h('div', { class: 'card-body' },
        h('strong', {}, c.id),
        c.busy ? h('span', { class: 'badge busy', title: `${c.busy.what} pid ${c.busy.pid}` }, '生成中') : null,
        h('p', {}, c.description),
        h('small', {}, `${c.action_names.length} 个动作 · spec r${c.spec_rev}${c.build ? ' · 已 build' : ''}`)))));
  if (!chars.length) cards.append(h('p', { class: 'muted' }, '还没有角色。'));

  const msg = h('div');
  const preview = h('img', { class: 'ref-preview', alt: '' });
  const fileInput = h('input', { type: 'file', name: 'ref', accept: 'image/png,image/webp,image/jpeg', required: true,
    onchange: () => {
      const f = fileInput.files?.[0];
      preview.src = f ? URL.createObjectURL(f) : '';
    } });
  const form = h('form', { class: 'grid-form' },
    h('label', {}, h('span', {}, '参考图（透明底会铺到 key 色上）'), fileInput),
    preview,
    h('label', {}, h('span', {}, 'id（a-z 0-9 -）'), h('input', { name: 'id', required: true, pattern: NAME_PATTERN })),
    h('label', { class: 'wide' }, h('span', {}, '描述词（每条提示词的主语）'), h('input', { name: 'description', required: true })),
    ...specFields(DEFAULTS, { lockKey: false }),
    h('div', { class: 'wide' }, h('button', { type: 'submit', class: 'primary' }, '新建角色')),
  );
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = new FormData();
    const file = fileInput.files?.[0];
    if (!file) return flash(msg, '请选参考图', 'error');
    data.append('ref', file);
    for (const [k, v] of Object.entries(formValues(form))) if (k !== 'ref') data.append(k, v);
    const btn = form.querySelector('button')!;
    btn.disabled = true;
    try {
      const { character } = await api.create(data);
      go(`#/c/${encodeURIComponent(character.id)}`);
    } catch (err) {
      msg.replaceChildren(errorBox(err));
    } finally {
      btn.disabled = false;
    }
  });

  root.replaceChildren(h('h1', {}, '角色'), cards, h('section', { class: 'panel' }, h('h2', {}, '新建角色'), msg, form));
}
