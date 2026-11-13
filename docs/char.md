# 角色 → 动作：`sprite-gen char` 需求

> Owns: 角色与动作的实体和账本（`sprite-gen char`）：锁定规格、不可变的生成版本、三样预览、Studio Web 的边界与验收 · Index: [docs/README.md](README.md)

状态：CLI（第 1–3 节）已实现，`sprite_gen/char/`，测试在 `tests/char/`；Studio Web（第 5 节）待做。

## 0. 为什么要这一层

现在一张底图能拍成视频、切成循环、导出 Phaser 图集（[video-pipeline.md](video-pipeline.md)、
[engine-export.md](engine-export.md)），但**没有「角色」这一层**：

- 角色的固定配置只活在 `work/<name>/character.json` + `scripts/myautosprite/char.ps1`，Python 不认识它。
  `char.ps1` 漏传 `--action-seconds` / `--body-height`，deer 那次是手补的（`work/deer/args.txt`）。
- 想重做一个动作只能手跑 `video-set --states X --force`，而 `video-set` 每次结束都**整份重写**
  `set.report.json` 和 `table.md`（`sprite_gen/video/batch.py` 第 1402、1435 行），其余动作的记录就丢了。
- 交付目录 `final/` 是手工复制拼起来的；提示词、原始视频和最终效果之间没有索引。

目标：补上「角色 → 动作 → 生成记录」的实体与账本。skill（agent）和 Studio Web **调同一套 CLI**；
Web 只负责编排和展示，最终效果用 Phaser 4 播放，与 TestPhaser 同一个引擎版本。

## 1. 调用形态：一切从 `sprite-gen char` 进

新增 verb 组 **`sprite-gen char`**，实现放在 `sprite_gen/char/`，在 `sprite_gen/_modules.py` 登记。

- 它是角色账本的**唯一实现**，skill 与 Web 都调它。
- 所有子命令都接受 `--json`：stdout 只输出一个 JSON 对象（成功或 `{"ok": false, "error": …}`），
  日志走 stderr，供 Node 侧解析。
- 生成复用现有 verb 的函数（`video-set`、`video-prompt`、`export-phaser`），**不复制流水线**。

| 子命令 | 作用 | 花钱 |
|---|---|---|
| `char new <id> --ref <png> --description "…" --body-height N [规格项…]` | 建角色：复制参考图；参考图带透明底时铺到纯色 key 上（`scripts/myautosprite/make_key_canvas.py` 的逻辑，收进 `sprite_gen/char/` 成为函数）；写 `character.json` | 否 |
| `char list` / `char show <id>` | 列角色 / 看角色（规格、动作、每个动作的当前版本与是否「旧规格」） | 否 |
| `char spec <id> [规格项…] [--description "…"]` | 改规格或描述词（见第 2 节「改规格」） | 否 |
| `char action add <id> <name> --state <s> --motion "…" [--action-seconds S] [--dry-run]` | 建动作并生成第 1 版；`--dry-run` 只打印将发送的完整提示词 | 是（dry-run 否） |
| `char action regen <id> <name> [--motion "…"] [--action-seconds S] [--dry-run]` | 重新生成：新开一版，旧版保留 | 是（dry-run 否） |
| `char action use <id> <name> <版本号>` | 把某个旧版设为当前 | 否 |
| `char action rm <id> <name> [--purge]` | 删除：默认整个动作目录移进 `work/chars/<id>/_trash/`；加 `--purge` 才物理删除 | 否 |
| `char build <id>` | 所有动作的当前版合成一张角色图集（`export-phaser`） | 否 |

**规则一：提示词预览必须和真发的一致。**
`--dry-run` 与真实生成走**同一个参数构建函数**，包括 `--action-seconds`、`--provider`、`--model`、
`--facing`、`--key`、`--body-plan`。`char.ps1 -DryRun` 现在调的是 `video-prompt` 且漏传其中几项，
预览因此失真，这正是要消灭的问题。

实现：`char_plan.video_set_argv` 是规格变成 flag 的唯一一处；`char_plan.planned_prompt` 按
`video-set` 内部的路径（`batch.run_item` → `batch.clip_prompt_parts`）拼提示词，**不走 `video-prompt`**
—— 后者默认按 Grok 的 model 拼，和 `video-set` 实际发的不一定一样。
`tests/char/test_char_plan.py` 用这份 argv 真跑 `video-set`（只拦下发片那一步），逐字比对；
真实生成结束后再拿 `clip.report.json#prompt` 比一次，结果记在 `version.json` 的 `prompt_match`。
deer 的 idle / walk / attack / death 四条已和当初真实发出的提示词逐字对上。

**规则二：skill 侧的改动。**

- SKILL.md 的执行路由表加一行：「角色 / 动作管理 → `char` → [char](char.md)」。
- 现有 verb 与它们的文档**一字不改**。
- `scripts/myautosprite/char.ps1` 在文件头与 [myautosprite.md](myautosprite.md) 中标记为「由 `char` 取代」。

## 2. 角色

**角色 = id + 参考图 + 描述词 + 锁定规格 + 动作集合。**
落盘在 `work/chars/<id>/character.json`，带 `"schema": "sprite-gen-char/1"`。id 只允许 `[a-z0-9-]`。

```
work/chars/<id>/
  character.json      schema、description、spec{…}、spec_rev、actions 顺序
  ref.png             用户给的参考图（原样）
  base.png            铺在 key 色上的底图（video-set --base side=base.png）
  actions/<name>/…    见第 3 节
  build/              char build 的产物
  _trash/             rm 移进来的动作
```

**锁定规格**：在角色上定义一次，动作只能继承。动作命令里出现这些参数 → **直接报错**，不是忽略。

| 字段 | 对应 video-set 参数 | 默认 | 为什么要统一 |
|---|---|---|---|
| `provider` / `model` | `--provider` `--model` | `dmx` / `doubao-seedance-2-0-260128` | 换模型会换画风，时长下限也不同 |
| `resolution` | `--resolution` | `480p` | 像素尺度一变，同一角色就合不成一张图集 |
| `direction` | `--base side=<base.png>` | `side`（v1 只支持 side） | 横版素材：用 sprite-gen 现成的侧视提示词 |
| `facing` | `--facing` | `right` | 本项目约定素材朝右 |
| `key` | `--key` | `green` | 抠像色，同时决定 `base.png` 铺的底色 |
| `body_plan` | `--body-plan` | `biped` | 漏了会凭空长出腿或手（青蛙、螃蟹都踩过） |
| `body_height` | `--body-height` | **必填** | `export-phaser` 拒收不同高度切出来的 strip |
| `fps` | `--out-fps` | 不设（按片源） | `export-phaser` 的 `frameRate` 取自 strip 自身的 fps，统一在这里定 |
| `walk_start` | `--walk-start` | `as-given` | 沿用 `char.ps1` 的写法 |

`fps` 不设时不传 `--out-fps`，帧率就是片源的（Seedance 是 24）。`video-loop` 要求 `--out-fps`
**严格高于**片源帧率，否则直接报错，所以默认值不能写成 24。设了就必须高于片源，靠 RIFE 补中间帧；
没装 RIFE 时 `video-set` 只警告不失败，`char` 原样转进该版 `version.json` 的 `warnings`。

`key` 一经建角色就不能改：`base.png` 就铺在这个 key 色上，换 key 请新建角色。

**不锁的**：画布形状（`--shape` square / wide / tall 由 video-set 按动作自动选），v1 不暴露；
最终 cell 尺寸由 `export-phaser` 统一，不影响合图。

**改规格**（`char spec`）：允许改，`spec_rev` 加一；**已有版本被标记为「旧规格」**（其
`version.json` 里的规格快照与当前不同），不参与 `char build`，需要重新生成。
只改描述词不算改规格（不加 `spec_rev`），但之后的新版会用新描述词。

## 3. 动作与生成记录

一个动作只持有：`name`、`state`（idle / walk / attack / death…，state 决定 sprite-gen 的提示词骨架）、
`motion` 文本、`action_seconds`（可选，对应 `--action-seconds <state>=S`）。

每次生成是一个**不可变的版本目录**：

```
work/chars/<id>/actions/<name>/
  action.json            定义 + 版本列表 + 当前版本号
  v1/  v2/ …             每版 = 一次 video-set --states <state> --out-dir vN
    side-<state>/clip.mp4                 原始视频
    side-<state>/clip.report.json         其中 "prompt" 字段 = 真正发出去的提示词
    side-<state>/loop/side-<state>.strip.* 切好的循环（png / json / gif / webp）
    preview/<id>.png / .json / .meta.json 该动作单独的 export-phaser 图集（最终效果用）
    version.json         用户输入的 motion、规格快照与 spec_rev、完整 argv、耗时、warnings、seam
```

- 每版独立一个 `--out-dir`，`set.report.json` 被整份重写的问题自然不存在。
- 版本号只增不复用；`use` 只改 `action.json` 的当前版本号，不动任何版本目录。
- 生成失败的版本也留目录，`version.json` 标 `ok: false` 和原因，不会被设为当前。
- 同一角色同时只允许一个作业：`work/chars/<id>/.lock`（记着 pid），已被占用时第二个命令立即报错，不发请求。
  持锁进程已经不在时（被强杀），下一个命令接管这把锁，并把它留下的 `running` 版本标成失败（interrupted）。
- `--dry-run` 不写任何文件。真实生成时，`video-set` 在子进程里跑，输出全部进 `vN/video-set.log`
  （Web 轮询它的末尾）；`char` 的 stdout 只有最后那一个 JSON。

## 4. 预览（三样都要能看）

对任一动作的任一版本，都能看到：

1. **原始提示词**：用户写的 motion，与 `clip.report.json#prompt`（实际发送的全文）并排显示。
2. **原始视频**：`clip.mp4`，可拖动、可逐帧。
3. **最终效果**：Phaser 4 加载该版 `preview/` 图集，按 `.meta.json` 的 anims 播放，画出脚底线和锚点。
   和 TestPhaser 的 `load.atlas` 走同一条链路：这里看对了，进游戏就是对的。

角色页另有一个「全部动作」舞台：加载 `char build` 的产物，逐个切换各动作的当前版，用来看脚底是否同线。

## 5. Studio Web（Node + Vite + Phaser 4 + TS）

放在 `MyAutoSprite/studio/`，整个仓库只有这里有 Node 依赖。

- **server**（`studio/server/`）
  - REST 接口：列角色、看角色、建角色（上传参考图）、改规格、建 / 重生成 / 删动作、设当前版本、build。
  - **每个接口内部都是 `spawn('<venv>/sprite-gen', ['char', …, '--json'])`**：不经 shell，参数白名单
    （id / name 按第 2 节字符集校验，其余字段只作为单个 argv 元素传入）。
  - 生成要跑 2–4 分钟：做成后台作业，页面轮询进度和 stderr 日志尾。依赖第 3 节的锁，同一角色同时只跑一个，
    重复点击不会二次付费。
  - 静态文件只发 `work/chars/` 下的，路径解析后必须仍在该目录内；mp4 支持 Range。
- **web**（`studio/web/`）
  - 三页：角色列表 → 角色详情 → 动作详情（提示词 / 原始视频 / 最终效果 / 历史版本）。
  - 外壳用普通 DOM（这里不是小游戏环境），预览区用一个 Phaser 4 实例。
- **启动**：`npm --prefix studio run dev`（Vite + server 一起起）。
- **花钱的按钮**：一律先走 `--dry-run` 显示完整提示词，再二次确认才真发。

## 6. 验收（逐条可测）

1. 未建角色不能建动作；动作命令带 `--resolution` 等任一锁定项会报错，且不发请求。
2. `char action add … --dry-run` 打印的提示词，与真实生成后 `clip.report.json#prompt` 逐字一致。
3. regen 后旧版仍在；`use` 回旧版后，三样预览都跟着切换。
4. `rm` 后目录在 `_trash/`，`char show` 不再列出该动作，其余动作的版本与当前号不受影响。
5. Web 上的每个操作，都能在终端用 `sprite-gen char …` 做出一样的结果（skill 与 Web 同源）。
6. 动作页 Phaser 播的图集文件，与该版 `preview/` 下的文件是同一份（同路径，不复制）。
7. `char spec` 改了锁定项后，`char show` 把已有版本标为「旧规格」，`char build` 跳过它们并在输出里列出。
8. 同一角色上一个生成作业未结束时，第二个 `add` / `regen` 立即报错。

**不做任何旧数据兼容**：全新功能，不导入 `work/<name>/` 下的旧角色，也不读旧格式的 `character.json`。
`character.json` 只有新格式一种，带 `schema` 字段。

## 7. 明确不做 / 以后再说

- 多方向（front / back）、帧级精修（继续用现有 [curation](curation.md)）。
- 「免费重切」：`video-loop --cycle fixed --start --length`。deer 的 attack 就是这样手调的，价值高，列为 v1.1。
- 「发布到 TestPhaser」：复制图集，再生成 `<name>.ts`。列为 v1.1。
