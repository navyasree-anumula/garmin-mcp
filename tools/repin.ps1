#Requires -Version 5.1
# Re-pin the Claude Desktop Garmin MCP containers.
# Every line here is kept short on purpose: a line that
# wraps on its way into a .ps1 breaks the file, and a wrap
# inside a quoted string breaks it silently.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$NewTag,
  [Parameter(Mandatory = $true)][string]$Digest,
  [string[]]$ExpectTools = @(),
  [int]$ExpectToolCount = 0,
  [string]$Repo = 'ghcr.io/navyasree-anumula/garmin-mcp',
  [string]$ConfigPath = '',
  [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Image = "${Repo}:${NewTag}"

# Where the config can be. The MSIX path was the only one
# hardcoded until 2026-11-22, when it stopped existing on the
# laptop and the script refused at step 3 -- correctly, but a
# guess that has gone stale once should become a search.
function Get-ConfigCandidates {
  $out = @()
  $leaf = 'claude_desktop_config.json'
  $ea = 'SilentlyContinue'

  # The standard installer.
  $a = Join-Path $env:APPDATA 'Claude'
  $out += Join-Path $a $leaf

  # Any MSIX package, rather than one package id.
  $pk = Join-Path $env:LOCALAPPDATA 'Packages'
  if (Test-Path -LiteralPath $pk) {
    $dirs = @(Get-ChildItem $pk -Filter 'Claude*' -Directory `
      -ErrorAction $ea)
    foreach ($d in $dirs) {
      $q = Join-Path $d.FullName 'LocalCache'
      $q = Join-Path $q 'Roaming'
      $q = Join-Path $q 'Claude'
      $out += Join-Path $q $leaf
    }
  }

  # Where Desktop actually keeps it as of 2026-11-22: a
  # Claude-Data directory holding one folder per package id,
  # OUTSIDE the MSIX Packages tree it used to live in.
  $cd = Join-Path $env:LOCALAPPDATA 'Claude-Data'
  if (Test-Path -LiteralPath $cd) {
    $out += Join-Path $cd $leaf
    $kids = @(Get-ChildItem $cd -Directory -ErrorAction $ea)
    foreach ($k in $kids) {
      $out += Join-Path $k.FullName $leaf
    }
  }

  return $out
}

if (-not $ConfigPath) {
  $cands = @(Get-ConfigCandidates)
  $found = @($cands | Where-Object {
    Test-Path -LiteralPath $_
  })

  if ($found.Count -eq 0) {
    Write-Host 'Looked for the Desktop config at:'
    $cands | ForEach-Object { Write-Host "    $_" }
    throw 'no config found. Pass -ConfigPath. Nothing changed.'
  }
  if ($found.Count -gt 1) {
    Write-Host 'More than one config exists:'
    $found | ForEach-Object { Write-Host "    $_" }
    throw 'ambiguous: pass -ConfigPath. Nothing changed.'
  }

  $ConfigPath = $found[0]
  Write-Host "Config: $ConfigPath"
}

function Step($n, $what) {
  Write-Host ''
  Write-Host "[$n] $what" -ForegroundColor Cyan
}
function Good($what) {
  Write-Host "    OK  $what" -ForegroundColor Green
}

if ($VerifyOnly) {
  Step 'verify' 'What is actually running'
  $fmt = '{{.Names}} | {{.Image}}'
  $running = @(docker ps --format $fmt)
  if ($running.Count -eq 0) {
    throw 'docker ps listed nothing. Desktop has not started.'
  }
  $running | ForEach-Object { Write-Host "    $_" }
  $new = @($running | Where-Object { $_.Contains($NewTag) })
  $old = @($running | Where-Object {
    $_.Contains($Repo) -and -not $_.Contains($NewTag)
  })
  if ($old.Count -gt 0) {
    throw "still on an older tag: $($old -join ' ; ')"
  }
  if ($new.Count -eq 0) {
    throw "nothing running is on $NewTag"
  }
  Good "$($new.Count) container(s) on $NewTag, none older"
  return
}

Step 1 "Pull and assert the digest"
Write-Host "    $Image"
$pulled = $false
foreach ($try in 1..3) {
  try {
    docker pull $Image | Out-Host
    $pulled = $true
    break
  } catch {
    Write-Warning "pull attempt $try failed"
    Start-Sleep -Seconds 5
  }
}
if (-not $pulled) {
  throw 'could not pull after 3 attempts. Nothing changed.'
}

$fmt = '{{json .RepoDigests}}'
$got = (@(docker image inspect $Image --format $fmt) -join '')
if ([string]::IsNullOrWhiteSpace($got)) {
  throw 'inspect returned no RepoDigests. Refusing a blind guard.'
}
if (-not $got.Contains($Digest)) {
  Write-Host "    inspect: $got"
  throw "DIGEST MISMATCH, wanted $Digest. Nothing changed."
}
Good 'digest matches'

Step 2 'Capability-probe inside the image'
# No quote characters inside this payload, and no long line:
# both are how a -c argument has been mangled here before.
$probe = 'import asyncio, garmin_mcp.server as s;'
$probe += 'ns = sorted('
$probe += 't.name for t in asyncio.run(s.mcp.list_tools()));'
$probe += 'print(len(ns));'
$probe += 'print(chr(10).join(ns))'
$probe = $probe -replace '\s+', ' '

$ep = '--entrypoint'
$out = @(docker run --rm $ep python $Image -c $probe)
if ($out.Count -lt 2) {
  throw "the probe printed nothing usable: $($out -join ' / ')"
}
$count = [int]$out[0]
$rest = @($out[1..($out.Count - 1)])
$names = @($rest | Where-Object { $_.Trim() -ne '' })
if ($names.Count -eq 0) {
  throw 'the probe listed no tools. Refusing an empty guard.'
}
Write-Host "    $count tool(s): $($names -join ', ')"

if ($ExpectToolCount -gt 0 -and $count -ne $ExpectToolCount) {
  throw "wanted $ExpectToolCount tools, got $count. Nothing changed."
}
if ($ExpectTools.Count -eq 0) {
  Write-Warning 'No -ExpectTools: no capability was asserted.'
} else {
  $joined = ($names -join ' ')
  foreach ($tool in $ExpectTools) {
    if (-not $joined.Contains($tool)) {
      throw "$tool is NOT in this image. Nothing changed."
    }
    Good "$tool is present"
  }
}

Step 3 'The config as it stands'
if (-not (Test-Path -LiteralPath $ConfigPath)) {
  Write-Host "    looked at: $ConfigPath"
  throw 'no config there. Nothing changed.'
}
$before = [System.IO.File]::ReadAllText($ConfigPath)
Write-Host $before

$pat = [regex]::Escape($Repo) + ':[^"]+'
$hits = ([regex]::Matches($before, $pat)).Count
if ($hits -eq 0) {
  throw 'the config names no such image. Nothing changed.'
}
Good "$hits image reference(s) to re-tag"

$wkey = 'GARMIN_MCP_ENABLE_WRITES'
$wBefore = ([regex]::Matches($before, $wkey)).Count
Write-Host "    $wkey appears $wBefore time(s)"

Step 4 'Back up, replace, assert before writing'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$ConfigPath.bak-$stamp"
Copy-Item -LiteralPath $ConfigPath -Destination $backup
Good "backed up to $backup"

$after = [regex]::Replace($before, $pat, $Image)
if (-not $after.Contains($Image)) {
  throw 'the replacement produced no new tag. Nothing written.'
}
$wAfter = ([regex]::Matches($after, $wkey)).Count
if ($wAfter -ne $wBefore) {
  throw "the edit changed $wkey. Nothing written."
}
try {
  $null = $after | ConvertFrom-Json
} catch {
  throw 'the edited config is not valid JSON. Nothing written.'
}
Good 'still valid JSON, writes flag intact'

Step 5 'Write it, with no BOM'
# Set-Content -Encoding UTF8 writes a BOM on 5.1, and this
# config has never had one.
$enc = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ConfigPath, $after, $enc)
$done = [System.IO.File]::ReadAllText($ConfigPath)
if ($done.Length -gt 0 -and $done[0] -eq [char]0xFEFF) {
  throw "a BOM was written. Restore from $backup."
}
if (-not $done.Contains($Image)) {
  throw "the file does not name the new tag. Restore $backup."
}
Good 'config re-pinned, no BOM'

Step 6 'Stop Claude Desktop'
$procs = @(Get-Process -Name Claude -ErrorAction SilentlyContinue)
if ($procs.Count -eq 0) {
  Write-Host '    Desktop was not running'
} else {
  $procs | Stop-Process
  Good "stopped $($procs.Count) process(es)"
}

Write-Host ''
Write-Host 'Now relaunch Desktop, then run:' -ForegroundColor Yellow
Write-Host "  .\repin.ps1 -NewTag $NewTag ``"
Write-Host "    -Digest $Digest -VerifyOnly"
Write-Host 'Then open a FRESH conversation.' -ForegroundColor Yellow
