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
  const fileInput = h('input', { type: 'file', name: 'ref', accept: REF_TYPES.join(','), required: true,
    onchange: () => {
      const f = fileInput.files?.[0];
      if (preview.src.startsWith('blob:')) URL.revokeObjectURL(preview.src);
      preview.src = f ? URL.createObjectURL(f) : '';
    } });
  // 拖进来 / 粘贴进来的图都塞回 <input type=file>，后面提交和预览只认这一处。
  const takeFile = (f: File): void => {
    const dt = new DataTransfer();
    dt.items.add(f);
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event('change'));
    msg.replaceChildren();
  };
  const form = h('form', { class: 'grid-form' },
    h('label', {}, h('span', {}, '参考图（可拖入或 Ctrl+V 粘贴；背景会自动抠掉再铺到 key 色上）'), fileInput),
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

  const panel = h('section', { class: 'panel' }, h('h2', {}, '新建角色'), msg, form);

  // 拖拽：整块面板都是落点。dragover 必须 preventDefault，否则浏览器会直接打开图片。
  let depth = 0; // dragenter/leave 会在子元素间反复触发，靠计数判断真正离开
  panel.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer?.types.includes('Files')) return;
    depth++;
    panel.classList.add('drop-target');
  });
  panel.addEventListener('dragleave', () => {
    if (--depth <= 0) { depth = 0; panel.classList.remove('drop-target'); }
  });
  panel.addEventListener('dragover', (e) => {
    if (e.dataTransfer?.types.includes('Files')) e.preventDefault();
  });
  panel.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    panel.classList.remove('drop-target');
    const f = pickImage(e.dataTransfer);
    if (f) takeFile(f);
    else flash(msg, '只支持 PNG / WebP / JPEG 图片', 'error');
  });

  // 粘贴：挂在 document 上，焦点在哪都能收到。剪贴板里没有图片时（比如往 id 框里贴文字）不拦。
  // 页面是整块替换的，离开本页后 form 脱离文档，监听器顺手把自己摘掉。
  const onPaste = (e: ClipboardEvent): void => {
    if (!form.isConnected) { document.removeEventListener('paste', onPaste); return; }
    const f = pickImage(e.clipboardData);
    if (!f) return;
    e.preventDefault();
    takeFile(f);
  };
  document.addEventListener('paste', onPaste);

  root.replaceChildren(h('h1', {}, '角色'), cards, panel);
}

const REF_TYPES = ['image/png', 'image/webp', 'image/jpeg'];

function pickImage(dt: DataTransfer | null | undefined): File | null {
  for (const f of Array.from(dt?.files ?? [])) if (REF_TYPES.includes(f.type)) return f;
  return null;
}
