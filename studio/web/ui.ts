// Tiny DOM helpers: the shell is plain DOM and TS, no UI framework.

type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;
type Child = Node | string | number | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = String(v);
    else if (k in el && typeof v !== 'string') (el as unknown as Record<string, unknown>)[k] = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

export function errorBox(e: unknown): HTMLElement {
  return h('pre', { class: 'error' }, e instanceof Error ? e.message : String(e));
}

export function flash(where: HTMLElement, node: Node | string, kind: 'ok' | 'error' | 'info' = 'info'): void {
  where.replaceChildren(h('div', { class: `flash ${kind}` }, node));
}

/** A modal; resolves true on confirm, false on cancel / Esc. */
export function confirmDialog(title: string, body: Node, confirmText: string, danger = false): Promise<boolean> {
  return new Promise((resolve) => {
    const dlg = h('dialog', { class: 'modal' });
    const done = (v: boolean) => {
      dlg.close();
      dlg.remove();
      resolve(v);
    };
    dlg.append(
      h('h3', {}, title),
      body,
      h('div', { class: 'actions' },
        h('button', { type: 'button', onclick: () => done(false) }, '取消'),
        h('button', { type: 'button', class: danger ? 'danger' : 'primary', onclick: () => done(true) }, confirmText)),
    );
    dlg.addEventListener('cancel', (e) => {
      e.preventDefault();
      done(false);
    });
    document.body.append(dlg);
    dlg.showModal();
  });
}

/** The full prompt a paid run will send, then a second confirm (docs/char.md section 5, 花钱的按钮). */
export function confirmSpend(what: string, prompt: string, argv: string[]): Promise<boolean> {
  const body = h('div', {},
    h('p', {}, `将要发送的完整提示词（${what}）：`),
    h('pre', { class: 'prompt' }, prompt),
    h('details', {}, h('summary', {}, 'video-set 参数'), h('pre', { class: 'argv' }, argv.join(' '))),
    h('p', { class: 'warn' }, '确认后会真的调用视频模型并计费，约 2–4 分钟。'));
  return confirmDialog('确认生成', body, '确认生成（花钱）', true);
}

export function fmtTime(s?: string | null): string {
  if (!s) return '';
  const d = new Date(s.replace(/([+-]\d{2})(\d{2})$/, '$1:$2'));
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString();
}

export const go = (hash: string) => {
  location.hash = hash;
};

/** Values of a form's named fields; empty strings left out. */
export function formValues(form: HTMLFormElement): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of new FormData(form).entries()) if (typeof v === 'string' && v.trim() !== '') out[k] = v.trim();
  return out;
}

/** ledger.NAME_RE as an input's pattern (the v flag browsers use wants the '-' in a class escaped). */
export const NAME_PATTERN = String.raw`[a-z0-9][a-z0-9\-]{0,47}`;
