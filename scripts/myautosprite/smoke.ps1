# 最小冒烟：用 grok 生成一张透明底 side-view 立绘，验证 key / 网络 / 端点是否通。
# 用法: pwsh -ExecutionPolicy Bypass -File scripts\myautosprite\smoke.ps1
# key 来自仓库根目录的 .env（sprite-gen 启动时自己读取，见 .env.example）；缺 key 时 sprite-gen 自己会报错。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$outDir = Join-Path $root 'work\smoke'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

& (Join-Path $root '.venv\Scripts\sprite-gen.exe') gen `
    --provider grok `
    --prompt 'a cute green slime creature, side view, facing right, full body, simple flat cartoon game sprite, plain green screen background' `
    --out (Join-Path $outDir 'slime.png') `
    --transparent `
    --direction side --facing right `
    --report (Join-Path $outDir 'slime.report.json')

Write-Host "exit=$LASTEXITCODE"
Get-ChildItem $outDir | Select-Object Name, Length
