# Engine export

> Owns: Aseprite-compatible export for Phaser and Flame; the Phaser character atlas from video-loop strips · Index: [docs/README.md](README.md)

`sprite-gen export-aseprite` describes a composed sprite-gen atlas using the JSON schema consumed by Aseprite loaders. It does not create an editable `.aseprite` source file and it does not re-encode the PNG.

Run it after `compose-atlas`:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-aseprite --run-dir <run-dir>
```

The default output is `<run-dir>/exports/aseprite.json`. It pairs with the existing `sprite-sheet-alpha.png` named by `manifest.json.game_input`. Because compose already applied `curation.json`, the export includes the final selected order, reused cells, transforms, pixel edits, clones, and breathe cells through the atlas rectangles it references.

## Mapping

| sprite-gen manifest | Aseprite-compatible JSON |
|---|---|
| `frame_layout.rows.<state>[i]` | `frames[]` rectangle in playback order |
| `animation.rows.<state>.durations_ms[i]` | frame `duration` in milliseconds |
| state name and global frame range | `meta.frameTags[]` |
| `game_input` | `meta.image` |
| `frame_layout.sheetWidth` / `sheetHeight` | `meta.size` |

Repeated playback instances keep repeated rectangles under distinct numeric frame keys. This preserves sprite-gen's shared-cell representation while presenting the sequential frame keys expected by engine loaders.

## Phaser

Phaser accepts Aseprite JSON in array or hash form and creates animations from `meta.frameTags`. The default `json-array` output uses stringified global frame indices, the tag ranges, and each frame's own duration.

```javascript
this.load.aseprite('hero', 'sprite-sheet-alpha.png', 'aseprite.json');
this.anims.createFromAseprite('hero');
this.add.sprite(100, 100, 'hero').play('idle');
```

Phaser's official API documents `load.aseprite` and `createFromAseprite`; its implementation looks up each tagged frame by the stringified numeric index and reads each frame's duration. Sources: [Phaser AnimationManager](https://docs.phaser.io/api-documentation/4.0.0/class/animations-animationmanager), [Phaser source](https://github.com/phaserjs/phaser/blob/master/src/animations/AnimationManager.js).

## Phaser, from video-loop strips — `export-phaser`

`export-aseprite` reads a composed run (pipeline A). The video pipeline ends each motion as a strip
(`<item>.strip.png` + `<item>.strip.json`, one cell size per state); `export-phaser` packs the strips of one
character into one atlas a Phaser game loads with `load.atlas`:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-phaser --name frog --out-dir <game>/public/atlas --flip --max-cell 256   --loop idle=<set>/side-idle/loop --loop walk=<set>/side-walk/loop --loop death=<set>/side-death/loop
```

It writes `frog.png`, `frog.json` (TexturePacker JSON hash) and `frog.meta.json`, published together, with
`frog.align.png`, an alignment preview to look at (the game does not load it).

| What | How |
|---|---|
| Frames | `<name>_<state>_NN`, every one the same untrimmed cell, 2 px of transparent margin in the cell and between cells |
| One origin | each state's ground point lands on one anchor, so `sprite.y` is the ground line in every animation. A periodic loop (strip `kind: periodic`: a walk, a sway): its lowest row and mean alpha centroid, where a few frames (at most a tenth) reaching well below all the others — an impact burst, dust under the feet — do not set the row and are reported hanging below `sprite.y`. Any other state — a cut played once, or a loop from a stance back to it (`pinned`, `fixed`: an idle, an attack) — its first frame's (a lunge, a slash or a fall moves away from where it started, and the anchor does not follow it) |
| `pivot` | every frame carries that origin; Phaser sets a sprite's origin from it on each frame change, and `setFlipX` mirrors about it (without a pivot Phaser mirrors inside the frame box, moving the feet by `w − 2·anchorX`) |
| `<name>.meta.json` | `cell`, `anchor`, `origin`, and per state `{key: "<name>-<state>", prefix, count, zeroPad, frameRate, repeat}`: `frameRate` is the strip's `fps`, `repeat` is `-1` for a loop and `0` for a cut played once (Phaser then holds the last frame) |
| `--flip` | mirrors every frame, for strips facing left in a game whose sprites face right |
| `--max-cell N` | scales the uniform cell (and its anchor) so its longer side is at most N px, premultiplied Lanczos; without it cells stay as cut, pixels moved by whole pixels only |

The strips must share one `body_h` (cut by one `video-set`, or with one `--body-height`); otherwise it refuses,
since the character would change size between animations. An atlas over 2048 px prints a warning; the uniform
cell is as tall as the highest jump and as wide as the longest lunge, so `--max-cell` or fewer frames is the remedy.

### Alignment check

Every export measures where each state stands once packed — its feet (the middle of the opaque columns just
above its lowest row) and its ground row: a periodic loop averaged over its stride, any other state on its
first frame — against `idle`'s (or the first state's). The report's `alignment` lists per state `feet_x` and
`ground` (from the anchor), `dx` and `dy` (from the reference), its `tolerance` and `ok`; a state further than
3 % of the reference's height (at least 2 px; twice that against a periodic loop, whose stance is a stride's mean)
prints a warning: the body would jump when the animation switches.

`<name>.align.png` shows it: one panel per state with the frame it starts on, the anchor's crosshair and the
ground line, and a last panel with every state's silhouette tinted and overlaid — one pair of feet when aligned.

A warning is fixed where the offset comes from, not in the atlas: a strip whose `kind` is wrong for its motion,
an effect drawn into the stance frame, a cut whose first frame is not the stance (cut it again from the stance).

```javascript
this.load.atlas('frog', 'atlas/frog.png', 'atlas/frog.json');
// in create(), for each anim of frog.meta.json:
this.anims.create({ key: a.key, frameRate: a.frameRate, repeat: a.repeat,
  frames: this.anims.generateFrameNames('frog', { prefix: a.prefix, start: 0, end: a.count - 1, zeroPad: a.zeroPad }) });
this.add.sprite(x, groundY, 'frog').play('frog-walk');   // origin comes from each frame's pivot
```

## Flutter and Flame

Flame's `SpriteAnimation.fromAsepriteData` expects `frames` to be a map and builds one animation from every value in that map. It does not read `meta.frameTags`. Export one hash file per state:

```bash
$SPRITE_GEN_ROOT/.venv/bin/sprite-gen export-aseprite --run-dir <run-dir> --format json-hash --split-states
```

This writes `exports/aseprite/idle.json`, `walk.json`, and so on. Every file uses local indices beginning at `0`, points to the same atlas PNG, and preserves millisecond frame durations. The relevant behavior is visible in Flame's official [`SpriteAnimation.fromAsepriteData` source](https://github.com/flame-engine/flame/blob/main/packages/flame/lib/src/sprite_animation.dart).

## Limits

- The exporter is structurally tested against the fields the loaders consume, but the repository does not run a browser Phaser app or Flutter runtime in CI.
- `loop` remains in `manifest.json`. Aseprite frame tags carry direction and range but no sprite-gen loop policy, and Flame's Aseprite factory constructs its default looping animation without reading tags.
- The curation webview is unchanged. This is a post-compose CLI export, not a new webview download button.
- The exporter refuses malformed row mappings and output paths outside the run directory. Split-state files are staged as a complete directory and then published together.

## Related

- [docs/README.md](README.md) — documentation index
