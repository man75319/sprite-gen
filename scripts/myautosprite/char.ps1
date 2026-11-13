# SUPERSEDED by `sprite-gen char` (docs/char.md): the spec locked on the character, every generation kept as a
# version, and a dry run that prints the prompt the run sends. Kept for the characters under work\<name>\.
#
# A character's clip settings, kept beside the character instead of retyped per run.
#
# sprite-gen's video verbs take the description as flags (`--character`, `--body-plan`, `--motion`): there is no
# character file it reads. This wrapper is that file: one JSON per character (see work\monster\character.json),
# turned into the flags `video-set` wants, so a run cannot forget the body plan and quietly film the wrong body.
#
# Provider keys come from the checkout's .env, which sprite-gen reads itself (see .env.example).
#
#   pwsh -File scripts\myautosprite\char.ps1 -Profile work\monster\character.json -States idle,walk -DryRun
#   pwsh -File scripts\myautosprite\char.ps1 -Profile work\monster\character.json -States idle,walk,attack,jump -OutDir work\monster\v3
param(
    [Parameter(Mandatory = $true)][string]$Profile,
    [string]$States = 'idle,walk,attack,jump',
    [string]$OutDir,
    [string]$Direction = 'side',
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exe = Join-Path $root '.venv\Scripts\sprite-gen.exe'
if (-not (Test-Path $exe)) { throw "no $exe - create the venv first (docs\myautosprite.md)" }
$spec = Get-Content (Join-Path $root $Profile) -Raw | ConvertFrom-Json

if ($spec.body_plan -notin @('biped', 'quadruped', 'legless')) { throw "body_plan must be biped/quadruped/legless, got '$($spec.body_plan)'" }

# `--motion STATE=TEXT`, one per state the profile describes.
$motion = @()
foreach ($state in $States.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) {
    $text = $spec.motions.$state
    if ($text) { $motion += "--motion"; $motion += "$state=$text" }
}

$common = @(
    '--provider', $spec.provider,
    '--model', $spec.model,
    '--base', "$($Direction)=$($spec.base)",
    '--states', $States,
    '--body-plan', $spec.body_plan,
    '--character', $spec.description,
    '--resolution', $spec.resolution,
    '--key', $spec.key,
    '--walk-start', 'as-given'
) + $motion

if ($DryRun) {
    # What each state's clip prompt would be — no request, no cost.
    foreach ($state in $States.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) {
        "===== $state ====="
        $bare = @('video-prompt', '--direction', $Direction, '--state', $state,
                  '--body-plan', $spec.body_plan, '--character', $spec.description)
        $text = $spec.motions.$state
        if ($text) { $bare += @('--motion', $text) }
        & $exe @bare
    }
    exit 0
}

if (-not $OutDir) { throw '-OutDir is required unless -DryRun' }
$run = @('video-set', '--out-dir', $OutDir) + $common
& $exe @run
exit $LASTEXITCODE
