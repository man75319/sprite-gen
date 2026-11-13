// 角色页的动作网格：每格循环播放一个动作当前版的 preview 图集（和动作页同一份 /files），点一格进动作详情。
// 它是自己的 Phaser.Game：动作页 / build 舞台那个（preview.ts）一次只放一张图集，这里要同时放 N 张。
// 纹理与动画是 Game 级别的，规矩同 preview.ts：重建前删掉上一轮的纹理、json、anims，key 带序号和时间戳。
import Phaser from 'phaser';
import type { AtlasFiles } from './preview.ts';

export interface Badge {
  text: string;
  color: number;
}

export interface GalleryItem {
  name: string;
  version: number | null;
  files: AtlasFiles | null;
  badges: Badge[];
  /** Shown in the cell when there is nothing to play. */
  note?: string;
}

interface AnimSpec {
  key: string;
  prefix: string;
  count: number;
  zeroPad: number;
  frameRate: number;
  repeat: number;
}

interface Meta {
  cell: [number, number];
  origin: [number, number];
  anims: AnimSpec[];
}

interface GalleryData {
  items: GalleryItem[];
  cols: number;
  onPick: (name: string) => void;
  done: () => void;
}

export const CELL_W = 220;
export const CELL_H = 240;
const GAP = 12;
const GROUND = CELL_H - 30; // the foot line inside a cell
const TOP = 52; // name and badges above
const FONT = 'system-ui, "Microsoft YaHei", sans-serif';

let loaded: { textures: string[]; metas: string[]; anims: string[] } = { textures: [], metas: [], anims: [] };
let seq = 0;

const url = (p: string, stamp: number) => `/files/${p.split('/').map(encodeURIComponent).join('/')}?t=${stamp}`;
const hex = (n: number) => `#${n.toString(16).padStart(6, '0')}`;

class GalleryScene extends Phaser.Scene {
  constructor() {
    super('gallery');
  }

  create(data: Partial<GalleryData>): void {
    if (!data.items) return;
    const { items, cols, onPick, done } = data as GalleryData;
    forget(this.game);
    const stamp = Date.now();
    const keys = items.map((it) => (it.files ? `g${++seq}-${stamp}` : null));
    items.forEach((it, i) => {
      const k = keys[i];
      if (!k || !it.files) return;
      this.load.atlas(k, url(it.files.png, stamp), url(it.files.json, stamp));
      this.load.json(`${k}-meta`, url(it.files.meta, stamp));
    });
    const draw = () => {
      items.forEach((it, i) => this.cell(it, keys[i], (i % cols) * (CELL_W + GAP), Math.floor(i / cols) * (CELL_H + GAP), onPick));
      done();
    };
    if (keys.some(Boolean)) {
      this.load.once(Phaser.Loader.Events.COMPLETE, draw);
      this.load.start();
    } else {
      draw();
    }
  }

  private cell(it: GalleryItem, key: string | null, x: number, y: number, onPick: (name: string) => void): void {
    const bg = this.add.graphics();
    const paint = (hover: boolean) => {
      bg.clear();
      bg.fillStyle(hover ? 0x262c37 : 0x1d2129, 1);
      bg.fillRect(x, y, CELL_W, CELL_H);
      bg.lineStyle(1, hover ? 0x3b82f6 : 0x2e3440, 1);
      bg.strokeRect(x + 0.5, y + 0.5, CELL_W - 1, CELL_H - 1);
      bg.lineStyle(1, 0xff4d4f, 0.7);
      bg.lineBetween(x + 8, y + GROUND, x + CELL_W - 8, y + GROUND);
    };
    paint(false);
    this.add.text(x + 10, y + 8, it.name, { color: '#e5e7eb', fontSize: '15px', fontStyle: 'bold', fontFamily: FONT });
    this.add.text(x + CELL_W - 10, y + 10, it.version ? `v${it.version}` : '—', { color: '#9ca3af', fontSize: '12px', fontFamily: 'Consolas, monospace' }).setOrigin(1, 0);
    let bx = x + 10;
    for (const b of it.badges) {
      const t = this.add.text(bx, y + 30, b.text, { color: '#111827', fontSize: '11px', fontFamily: FONT, backgroundColor: hex(b.color), padding: { x: 5, y: 1 } });
      bx += t.width + 4;
    }

    const meta = key && this.textures.exists(key) && this.cache.json.exists(`${key}-meta`) ? (this.cache.json.get(`${key}-meta`) as Meta) : null;
    if (key && meta && meta.anims.length) {
      loaded.textures.push(key);
      loaded.metas.push(`${key}-meta`);
      const a = meta.anims[0];
      const animKey = `${key}:${a.key}`;
      if (this.anims.exists(animKey)) this.anims.remove(animKey);
      this.anims.create({
        key: animKey,
        frames: this.anims.generateFrameNames(key, { prefix: a.prefix, start: 0, end: a.count - 1, zeroPad: a.zeroPad }),
        frameRate: a.frameRate,
        repeat: -1, // a one-shot (death) would stop on its last frame; the grid is for looking, so it loops
        repeatDelay: a.repeat === 0 ? 600 : 0,
      });
      loaded.anims.push(animKey);
      const [cw, ch] = meta.cell;
      const [ox, oy] = meta.origin;
      const scale = Math.min(1, (CELL_W - 20) / cw, (GROUND - TOP) / (ch * oy));
      this.add.sprite(x + CELL_W / 2, y + GROUND, key).setOrigin(ox, oy).setScale(scale).play(animKey);
    } else {
      this.add.text(x + CELL_W / 2, y + (TOP + GROUND) / 2, it.note ?? (key ? '图集加载失败' : '还没有可用版本'),
        { color: '#9ca3af', fontSize: '13px', fontFamily: FONT, align: 'center', wordWrap: { width: CELL_W - 24 } }).setOrigin(0.5);
    }

    this.add.zone(x, y, CELL_W, CELL_H).setOrigin(0, 0).setInteractive({ useHandCursor: true })
      .on('pointerover', () => paint(true))
      .on('pointerout', () => paint(false))
      .on('pointerup', () => onPick(it.name));
  }
}

function forget(game: Phaser.Game): void {
  for (const k of loaded.anims) if (game.anims.exists(k)) game.anims.remove(k);
  for (const k of loaded.textures) if (game.textures.exists(k)) game.textures.remove(k);
  for (const k of loaded.metas) game.cache.json.remove(k);
  loaded = { textures: [], metas: [], anims: [] };
}

let game: Phaser.Game | null = null;
const host = document.createElement('div');
host.className = 'phaser-host gallery-host';

/** Puts the gallery canvas into `slot` (moved, never recreated) and lays the items out to its width. */
export function showGallery(slot: HTMLElement, items: GalleryItem[], onPick: (name: string) => void): Promise<void> {
  slot.replaceChildren(host);
  const width = Math.max(CELL_W, slot.clientWidth || CELL_W * 4);
  const cols = Math.max(1, Math.min(items.length, Math.floor((width + GAP) / (CELL_W + GAP))));
  const w = cols * (CELL_W + GAP) - GAP;
  const h = Math.max(1, Math.ceil(items.length / cols)) * (CELL_H + GAP) - GAP;
  if (!game) {
    game = new Phaser.Game({ type: Phaser.AUTO, parent: host, width: w, height: h, backgroundColor: '#15181e', scene: [GalleryScene], banner: false,
      input: { mouse: { preventDefaultWheel: false } } }); // let the wheel scroll the page over the canvas
  } else if (game.scale.width !== w || game.scale.height !== h) {
    game.scale.resize(w, h);
  }
  const g = game;
  return new Promise((resolve) => {
    const start = () => g.scene.start('gallery', { items, cols, onPick, done: resolve } satisfies GalleryData);
    if (g.isBooted) start();
    else g.events.once(Phaser.Core.Events.READY, start);
  });
}
