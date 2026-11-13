// The one Phaser 4 game on the page (docs/char.md section 4, 最终效果): it loads an export-phaser atlas
// (png + json + meta.json) straight from /files — the same files TestPhaser's load.atlas takes — and plays
// meta.anims, with the foot line and the anchor drawn.
//
// Textures and animations belong to the Game, not to a scene: before each load the previous texture, its json
// and its anims are removed, and every key carries a stamp, or switching versions keeps showing the old image.
import Phaser from 'phaser';

export interface AtlasFiles {
  png: string;
  json: string;
  meta: string;
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
  name: string;
  cell: [number, number];
  anchor: [number, number];
  origin: [number, number];
  anims: AnimSpec[];
}

export interface Shown {
  /** The anim keys as meta.json names them (texture-independent), in its order. */
  anims: string[];
  meta: Meta;
}

const W = 520;
const H = 340;
const FOOT_Y = H - 50;

interface ShowData {
  files: AtlasFiles;
  play?: string;
  resolve: (s: Shown) => void;
  reject: (e: Error) => void;
}

/** What the scene put into the Game, removed before the next load. */
let loaded: { texture: string; metaKey: string; anims: string[]; prefix: string } | null = null;
let seq = 0;

const files = (p: string, stamp: number) => `/files/${p.split('/').map(encodeURIComponent).join('/')}?t=${stamp}`;

class PreviewScene extends Phaser.Scene {
  private sprite?: Phaser.GameObjects.Sprite;
  private label?: Phaser.GameObjects.Text;

  constructor() {
    super('preview');
  }

  create(data: Partial<ShowData>): void {
    this.sprite = undefined; // the same scene object is restarted: last time's sprite is destroyed
    this.label = undefined;
    this.drawGround();
    if (!data.files) {
      this.add.text(W / 2, H / 2, '没有图集', { color: '#888', fontSize: '16px' }).setOrigin(0.5);
      return;
    }
    const { files: f, resolve, reject } = data as ShowData;
    forget(this.game);
    const stamp = Date.now();
    const texture = `atlas-${++seq}-${stamp}`;
    const metaKey = `${texture}-meta`;
    this.load.atlas(texture, files(f.png, stamp), files(f.json, stamp));
    this.load.json(metaKey, files(f.meta, stamp));
    this.load.once(Phaser.Loader.Events.FILE_LOAD_ERROR, (file: Phaser.Loader.File) => {
      reject(new Error(`加载失败：${file.src}`));
    });
    this.load.once(Phaser.Loader.Events.COMPLETE, () => {
      if (!this.textures.exists(texture) || !this.cache.json.exists(metaKey)) return; // the error above said why
      const meta = this.cache.json.get(metaKey) as Meta;
      const prefix = `${texture}:`;
      const anims: string[] = [];
      for (const a of meta.anims) {
        const key = prefix + a.key;
        if (this.anims.exists(key)) this.anims.remove(key);
        this.anims.create({
          key,
          frames: this.anims.generateFrameNames(texture, { prefix: a.prefix, start: 0, end: a.count - 1, zeroPad: a.zeroPad }),
          frameRate: a.frameRate,
          repeat: a.repeat,
        });
        anims.push(a.key);
      }
      loaded = { texture, metaKey, anims: anims.map((k) => prefix + k), prefix };
      this.place(meta, texture);
      const first = data.play && anims.includes(data.play) ? data.play : anims[0];
      if (first) this.playKey(first);
      resolve({ anims, meta });
    });
    this.load.start();
  }

  private drawGround(): void {
    const g = this.add.graphics();
    g.lineStyle(1, 0x3a3f4b, 1);
    for (let x = 0; x < W; x += 20) g.lineBetween(x, 0, x, H);
    for (let y = 0; y < H; y += 20) g.lineBetween(0, y, W, y);
  }

  /** The sprite with meta.origin at the foot line, scaled to fit; the cell box, the foot line and the anchor cross. */
  private place(meta: Meta, texture: string): void {
    const [cw, ch] = meta.cell;
    const [ox, oy] = meta.origin;
    const scale = Math.min(1, (W - 40) / cw, (FOOT_Y - 16) / (ch * oy), (H - FOOT_Y - 8) / Math.max(1, ch * (1 - oy)) || 1);
    const x = W / 2;
    const g = this.add.graphics();
    g.lineStyle(1, 0x6b7280, 0.8);
    g.strokeRect(x - cw * ox * scale, FOOT_Y - ch * oy * scale, cw * scale, ch * scale);
    this.sprite = this.add.sprite(x, FOOT_Y, texture).setOrigin(ox, oy).setScale(scale);
    const top = this.add.graphics();
    top.lineStyle(2, 0xff4d4f, 1);
    top.lineBetween(0, FOOT_Y, W, FOOT_Y); // foot line: meta.anchor's row
    top.lineStyle(2, 0x22d3ee, 1);
    top.lineBetween(x - 10, FOOT_Y, x + 10, FOOT_Y);
    top.lineBetween(x, FOOT_Y - 10, x, FOOT_Y + 10); // anchor cross: meta.anchor
    this.label = this.add.text(8, 8, '', { color: '#e5e7eb', fontSize: '13px', fontFamily: 'monospace' });
    this.add.text(8, H - 22, `cell ${cw}×${ch}  anchor ${meta.anchor.join(',')}  origin ${ox},${oy}  ×${scale.toFixed(2)}`,
      { color: '#9ca3af', fontSize: '12px', fontFamily: 'monospace' });
  }

  playKey(key: string): void {
    if (!this.sprite || !loaded) return;
    this.sprite.play(loaded.prefix + key);
    this.label?.setText(key);
  }

  override update(): void {
    if (this.sprite?.anims?.currentFrame && this.label && loaded) {
      const a = this.sprite.anims;
      this.label.setText(`${a.currentAnim?.key.slice(loaded.prefix.length)}  ${a.currentFrame?.index}/${a.getTotalFrames()}`);
    }
  }
}

function forget(game: Phaser.Game): void {
  if (!loaded) return;
  for (const key of loaded.anims) if (game.anims.exists(key)) game.anims.remove(key);
  if (game.textures.exists(loaded.texture)) game.textures.remove(loaded.texture);
  game.cache.json.remove(loaded.metaKey);
  loaded = null;
}

let game: Phaser.Game | null = null;
const host = document.createElement('div');
host.className = 'phaser-host';

function ensureGame(): Phaser.Game {
  if (game) return game;
  game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: host,
    width: W,
    height: H,
    backgroundColor: '#1f232b',
    scene: [PreviewScene],
    banner: false,
  });
  return game;
}

/** Puts the one Phaser canvas into `slot` (moved, never recreated), and shows the atlas there. */
export function show(slot: HTMLElement, f: AtlasFiles | null, play?: string): Promise<Shown | null> {
  slot.replaceChildren(host);
  const g = ensureGame();
  return new Promise((resolve, reject) => {
    const start = () => {
      if (!f) {
        g.scene.start('preview', {});
        resolve(null);
        return;
      }
      g.scene.start('preview', { files: f, play, resolve, reject } satisfies ShowData); // restarts it: a fresh scene
    };
    if (g.isBooted) start();
    else g.events.once(Phaser.Core.Events.READY, start);
  });
}

export function play(key: string): void {
  const scene = game?.scene.getScene('preview') as PreviewScene | undefined;
  scene?.playKey(key);
}
