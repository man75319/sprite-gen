// Hash router: #/ → 角色列表, #/c/:id → 角色详情, #/c/:id/a/:name[/v/:n] → 动作详情.
import { api } from './api.ts';
import { actionPage } from './pages/action.ts';
import { characterPage } from './pages/character.ts';
import { listPage } from './pages/list.ts';
import { h } from './ui.ts';
import './style.css';

const root = document.getElementById('app')!;
let cleanup: (() => void) | void = undefined;
let seq = 0;

async function route(): Promise<void> {
  const mine = ++seq;
  if (cleanup) cleanup();
  cleanup = undefined;
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  let done: (() => void) | void;
  if (parts[0] === 'c' && parts[1] && parts[2] === 'a' && parts[3]) {
    done = await actionPage(root, parts[1], parts[3], parts[4] === 'v' && parts[5] ? Number(parts[5]) : null);
  } else if (parts[0] === 'c' && parts[1]) {
    done = await characterPage(root, parts[1]);
  } else {
    done = await listPage(root);
  }
  if (mine === seq) cleanup = done;
  else if (done) done(); // navigated away while it loaded
  window.scrollTo({ top: 0 });
}

window.addEventListener('hashchange', () => void route());
void route();

api.server().then((s) => {
  document.getElementById('server-info')!.replaceChildren(
    h('span', { class: 'mono' }, s.charsDir), s.fake ? h('span', { class: 'badge stale' }, 'FAKE：不花钱，video-set 是替身') : '');
}, () => {
  document.getElementById('server-info')!.textContent = 'server 未启动（npm --prefix studio run dev）';
});
