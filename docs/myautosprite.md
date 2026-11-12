# MyAutoSprite: this fork's setup and additions

This checkout is `aldegad/sprite-gen` **v4.3.0** (Apache-2.0) with a **`dmx` provider** added (images: Doubao
Seedream; clips: Doubao Seedance, through DMXAPI's `/v1/responses`), a fix for an old bug that swallowed
`--no-audio`, and a few scripts of its own under `scripts/myautosprite/`.

## Setting up a machine

Everything the project uses is in git except three things that belong to one machine:

| What | Where | How to get it |
|---|---|---|
| Python venv | `.venv\` | Python 3.12 (`uv python install 3.12`), then `uv venv .venv --python 3.12` and `uv pip install -e .` in the checkout ([interpreter](interpreter.md)) |
| Provider keys and relay settings | `.env` (ignored by git) | `copy .env.example .env`, then fill in `DMX_API_KEY` |
| ffmpeg / ffprobe, img2webp / cwebp | `.tools\ffmpeg\bin\`, `.tools\webp\bin\` (ignored by git) | unpack an ffmpeg build and Google's libwebp release there; `video` and `video-frames` need ffmpeg, `video-loop` needs img2webp for its WebP |

`sprite-gen` reads `.env` and puts `.tools\*\bin` first on PATH when it starts (`sprite_gen/util/env_file.py`), so
every verb runs as is, from PowerShell, bash or an agent: `.venv\Scripts\sprite-gen.exe <verb> ...`. A variable
already set in the shell wins over the file; `SPRITE_GEN_ENV_FILE=<path>` reads another file, `off` none. Only the
`sprite-gen` command does this: a module run as `python -m sprite_gen....` or a `scripts\*.py` wrapper reads
neither, so give those the keys from the shell.

Check the keys, the network and the relay with one image (one paid call):
`pwsh -ExecutionPolicy Bypass -File scripts\myautosprite\smoke.ps1`.

## Three ways to make pictures

**A. Images: the `openai` provider (unchanged)** — DMXAPI's OpenAI-compatible endpoint, `gpt-image-2.5-flare` by
default (`SPRITE_GEN_IMAGE_MODEL` in `.env`), native alpha.

**B. Images: the `dmx` provider (new)** — Doubao Seedream through `/v1/responses`, no alpha (chroma cut-out).

```powershell
.venv\Scripts\sprite-gen.exe gen --provider dmx --model doubao-seedream-5-0-pro-260628 `
  --prompt "..." --out fox.png --transparent
```

**C. Clips: `--provider dmx` (new)** — Doubao Seedance, also through `/v1/responses` (the request returns a task
id, then `seedance-2-0-get` turns it into a video_url).

```powershell
.venv\Scripts\sprite-gen.exe video --provider dmx --model doubao-seedance-2-0-260128 `
  --image work\smoke\fox-green.png --prompt "the fox breathes in place, locked-off camera" `
  --duration 4 --resolution 480p --no-audio --out work\smoke\fox-idle.mp4
```

Optional settings for `dmx`, in `.env`: `SPRITE_GEN_DMX_BASE_URL` (another relay, up to `/v1`),
`SPRITE_GEN_DMX_VISION_MODEL` (the facing and logo vision checks, e.g. `doubao-seed-2-1-pro-260915`),
`SPRITE_GEN_DMX_VIDEO_QUERY_MODEL` (the model the task is asked through, `seedance-2-0-get`).

## Pipeline B (clip to sprite loop)

**The still is opaque, on a flat key.** A cut-out (a transparent PNG, what `gen --transparent` returns) reaches the
video model as the colour under its clear pixels, most often black, and no key cuts that clip (2026-11-12: four
Seedance clips of a crab on black, paid for and thrown away). `video-canvas` and `video-set` now refuse a still with
transparent pixels before any paid call; put it on a key first:

```powershell
# 0) a cut-out put on flat green, its lowest pixel on --baseline, --height tall (faint edge specks dropped: --alpha-floor)
.venv\Scripts\python.exe scripts\myautosprite\make_key_canvas.py --source base.png --out base-green.png --height 400 --baseline 840
```

```powershell
# 1) the still: drawn on flat green or magenta already, or given a border by video-canvas (four corners one flat key)
.venv\Scripts\sprite-gen.exe video-canvas --still base-green.png --state idle --key green --out canvas.png
# 2) the clip
.venv\Scripts\sprite-gen.exe video --provider dmx --image canvas.png --prompt "..." --duration 4 --resolution 480p --no-audio --out clip.mp4
# 3) frames keyed, 4) the cycle found and cut
.venv\Scripts\sprite-gen.exe video-frames --clip clip.mp4 --out-dir frames --spill auto --reference base-green.png
.venv\Scripts\sprite-gen.exe video-loop --frames-dir frames\keyed --out-dir loop --state idle --fps 24
```

A whole set (directions x states): `video-set --provider dmx --base side=base.png --states idle,walk,run ...` (without
`--duration`, each state's default 3 s / 2 s is raised to Seedance's 4 s floor). `video` and `video-set` default to
**480p** (measured 2026-11-11 on the frog: good enough, about half the cost by pixels; ask `--resolution 720p` for
detail). A walk whose feet float over part of its stride: `--ground-pin on` ([video-pipeline](video-pipeline.md)).

## A short action in a long clip (`--action-seconds`)

Seedance films 4 s at least, and an attack lasts about a second. Pinned to its first frame (the default for an attack),
the clip struck once and held its impact pose for about a second, which had to be cut out by hand (`video-chain`).
`--action-seconds STATE=S` gives the length of one motion instead, and the engine works the rest out from the provider:

| | `--action-seconds attack=1`, dmx | `attack=1.5`, dmx | `attack=1`, xai |
|---|---|---|---|
| clip (`--duration` wins) | 4 s, Seedance's floor | 4 s | 2 s, two of it |
| the prompt asks for | 4 in a row | 3 in a row | 2 in a row |
| pin / cut | none / the loop searched (`periodic`) | same | same |

The built-in attack sentence's phase timings are scaled to add up to S. The count steers the pace; it is not obeyed:
asked for 3 in 4 s, Seedance gave 4 three times out of three (2026-11-12, crab, English and Chinese), and each cut was a
whole, closing attack (one strike, or a two-strike combo). The item's record says `action`: `repeats_asked`,
`repeats_seen` (clip frames over the cut) and `cut_seconds`. Preview without a request:
`sprite-gen video-prompt --direction side --state attack --provider dmx --action-seconds 1`.

## The body plan (`--body-plan`): a monster is not a person

**Many legs (a crab, a spider, an insect): `biped` and the legs in `--motion`.** There are three plans and none has
more than four legs: `quadruped` says "it stays on all four legs" and `legless` "it has no legs", both wrong for eight.
`biped` adds no leg count, so the crab (2026-11-12, `work/crab/character.json`) was filmed as `biped` with its eight
legs and two claws named in every motion text. The cost: an attack under `biped` still carries the grip sentence ("one
hand stays one hand"), which names hands the crab has not; it filmed well regardless.

sprite-gen's default body is **biped (a person on two feet)**. Its attack sentence says "bare hands only if it holds
nothing ... one hand stays one hand, both hands stay both hands", so a frog or a slime, which has no hands, is given
**arms** by the model.

A body that is not a person takes `--body-plan legless` (or `quadruped`), and the engine changes three sentences:

| | default biped | `--body-plan legless` |
|---|---|---|
| attack | "with what it is already holding (**bare hands** ...)" | "...or with its own body if it holds nothing" |
| holding | "one hand stays one hand, **both hands** stay both hands" | "**Anything it holds or carries** stays as shown" |
| body | — | "**It has no legs** ... never grows legs or feet" |

Reading the prompt costs nothing: `sprite-gen video-prompt --direction side --state attack --body-plan legless --character "..."`.
To take the motion over, add `--motion "attack=lunges forward and bites once, jaws snapping"` (per state or per item).

**Measured on the frog**: `quadruped` gave better seam numbers (attack 0.13 / walk 0.51), but its motion looked less
right and less smooth than `legless`; so `legless` was chosen by eye, and "four short legs" taken out of the
description, which would contradict "It has no legs ... never grows legs or feet". The lesson: **a seam is one
reading, the picture decides**; write the chosen body plan into the character's profile instead of remembering it.

A character's profile and its wrapper: a `character.json` beside the character's work (provider, model, base,
body_plan, description, resolution, key, motions) and `scripts\myautosprite\char.ps1` (`-DryRun` prints the prompts;
`-OutDir` runs the set).

## Scripts of this fork (`scripts/myautosprite/`)

| Script | What it does |
|---|---|
| `char.ps1` | A character's `character.json` turned into `video-set` flags, so a run cannot forget the body plan |
| `smoke.ps1` | One transparent side-view image through grok: are the key, the network and the endpoint working |
| `flood_cut.py` | A flat background cut off a drawing by flood fill from the border only (art whose colours sit close to the paper) |
| `make_key_canvas.py` | A cut-out drawing put on a flat chroma canvas, framed for `sprite-gen video`; pixels at alpha 64 or under dropped (`--alpha-floor`), so a generator's edge specks do not widen the subject's box and shrink it |
| `make_sheet.py` | One row per state out of the loops `video-set` cut, for a single look at the whole set |

## Verified

| Route | Result |
|---|---|
| `openai` + gpt-image-2.5-flare | 4 rows, 16 frames, `inspect` 0 errors / warnings |
| `dmx` + Seedream (images) | 4 rows, 16 frames, `inspect` 0 errors / warnings, ¥0.3 an image |
| `dmx` + Seedance (clips) | 4.1 s / 640x640 / 97 frames in 154 s; `video-frames` keyed 97 frames; `video-loop` cut a 63-frame loop at 18 fps with strip / GIF / WebP |
| A hand-drawn monster, four states | idle / walk / jump / attack (legless, a bite), seam 2.02 / 1.25 / 1.00 / 0.20 |

## What this fork changed in the source

| File | Change |
|---|---|
| `sprite_gen/gen/dmx_provider.py` | **New**: Seedream images (`/responses` transport, size mapping, reference images, chroma sentence, b64/url replies) |
| `sprite_gen/gen/dmx_video.py` | **New**: Seedance clips (submit, poll, download, verify and publish; the task id is never lost) |
| `sprite_gen/gen/base.py` | `PROVIDER_DMX`; `GenRequest.chroma_key` |
| `sprite_gen/gen/address.py` | the `dmx` address and `SPRITE_GEN_DMX_BASE_URL` |
| `sprite_gen/gen/__init__.py` | the provider registered, `_make_provider`, explicit selection, `chroma_key` passed on |
| `sprite_gen/gen/video.py` | `VideoRequest.provider`, `--provider {xai,dmx}`, `_generate_dmx`, the report's `provider` |
| `sprite_gen/video/batch.py` | `video-set --provider/--model`, the 4 s floor for dmx, `--ground-pin` |
| `sprite_gen/video/loop.py` | `video-loop --ground-pin` |
| `sprite_gen/video/batch.py`, `clip_prompt.py` | `--action-seconds`: a motion's own length, the clip and the count worked out from the provider's shortest clip |
| `sprite_gen/video/canvas.py`, `batch.py` | a still with transparent pixels refused before any paid call (`refuse_transparent`) |
| `sprite_gen/video/chain.py` | a `video-set` item's frames report, one folder up from its keyed/, read for the key and the rate |
| `sprite_gen/video/nonloop.py` | a death or fall no longer has to end lower than it stood (a crab on its back is taller); its end pose only has to differ from its first |
| `sprite_gen/compose/export_phaser.py` | the feet origin read per kind of loop, the alignment check and `<name>.align.png` |
| `sprite_gen/util/env_file.py` | **New**: the checkout's `.env` and `.tools` read on start |
| `sprite_gen/util/cli_kwargs.py` | fix: `--audio/--no-audio` sharing a dest overwritten by its default |
| `sprite_gen/gen/refusal.py`, `retry.py`, `workflow/{catalog,access,capabilities}.py` | the new provider's table entries, labels and access probe |
| `docs/gen.md`, `docs/video.md` | provider table, address table, Seedance differences |

## Known limits

1. **Clips are image-to-video only**: Seedance text-to-video is not wired, `video` still needs `--image` or `--last-frame`.
2. `video-extend` / `video-edit` / `video-resume` are xAI only; the relay has no such endpoints.
3. On Windows, pytest-timeout runs by thread: one test past its time ends the whole run, so run a long domain alone.
