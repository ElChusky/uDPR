# BDSP PC rebuild - one-command project setup (started by setup.cmd in the repository root).
#
#   setup.cmd [-Dump <folder with exefs\ and romfs\>] [-Unity <...\2019.4.27f1\Editor\Unity.exe>] [-Wwise <Unity project with the
#             Launcher's Wwise integration>] [-Work <folder>] [-BatchMode]
#
# What it does (each part is skipped when already done, so running it again resumes):
#   1. finds Unity 2019.4.27f1 (Unity Hub's install folders); if missing, opens Unity Hub on that exact version and stops;
#   2. asks for the game dump folder (exefs\main + romfs\Data of BDSP v1.3.0) unless -Dump is given;
#   3. downloads its own tools into SetupWork\bin, each checked against a pinned SHA-256: Python 3.13.12 (python.org's NuGet
#      package - no installer, nothing registered) + the Python packages in setup\requirements.lock, AssetRipper 1.3.14 (free);
#   4. Wwise: creates SetupWork\WwiseIntegration (an empty Unity project), shows the Audiokinetic Launcher steps and waits until
#      Wwise 2019.2.5 is integrated there (or uses -Wwise);
#   5. runs setup\setup.py: export -> ... -> verify (several hours; Unity windows open and close by themselves).
param(
    [string]$Dump,
    [string]$Unity,
    [string]$Wwise,
    [string]$Work,
    [switch]$BatchMode
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # Invoke-WebRequest is many times slower with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Project = Split-Path -Parent $PSScriptRoot
if (-not $Work) { $Work = Join-Path $Project 'SetupWork' }
$Bin = Join-Path $Work 'bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null

$UnityVersion = '2019.4.27f1'
$UnityHubLink = 'unityhub://2019.4.27f1/23dc10685eb4'
$PythonUrl = 'https://www.nuget.org/api/v2/package/python/3.13.12'
$PythonSha = '389c0228afbb3530f6cd079f5cec44139b1441dd51c85ec704f7a63f1c9f3802'
$RipperUrl = 'https://github.com/AssetRipper/AssetRipper/releases/download/1.3.14/AssetRipper_win_x64.zip'
$RipperSha = '808cddf66dd0357ad6b36b97de3a2aef5e3552e63af3ee0610f9a03a0378101c'

function Say($msg) { Write-Host ("[setup] " + $msg) }
function Fail($msg) { Write-Host ("[setup] PROBLEM: " + $msg) -ForegroundColor Red; exit 1 }

function Get-Pinned($url, $sha, $dest) {
    if ((Test-Path $dest) -and ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $sha.ToUpper())) { return }
    Say "downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash
    if ($got -ne $sha.ToUpper()) { Remove-Item $dest; Fail "checksum mismatch for $url (got $got)" }
}

# 1. Unity
if (-not $Unity) {
    $roots = @(Join-Path $env:ProgramFiles 'Unity\Hub\Editor')
    $secondary = Join-Path $env:APPDATA 'UnityHub\secondaryInstallPath.json'
    if (Test-Path $secondary) {
        $p = (Get-Content $secondary -Raw).Trim().Trim('"')
        if ($p) { $roots += $p }
    }
    foreach ($r in $roots) {
        $c = Join-Path $r "$UnityVersion\Editor\Unity.exe"
        if (Test-Path $c) { $Unity = $c; break }
    }
}
if (-not $Unity -or -not (Test-Path $Unity)) {
    Say "Unity $UnityVersion is not installed. Opening Unity Hub on that version - install it (add 'Windows Build Support (IL2CPP)'"
    Say "only if you want to build a standalone game), then run setup.cmd again."
    Start-Process $UnityHubLink
    exit 1
}
Say "Unity: $Unity"

# 2. the dump
$cfgPath = Join-Path $Work 'config.json'
if (-not $Dump -and (Test-Path $cfgPath)) { $Dump = (Get-Content $cfgPath -Raw | ConvertFrom-Json).dump }
if (-not $Dump) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Select your BDSP v1.3.0 dump: the folder that holds exefs and romfs'
    if ($dlg.ShowDialog() -ne 'OK') { Fail 'no dump folder selected' }
    $Dump = $dlg.SelectedPath
}
if (-not (Test-Path (Join-Path $Dump 'exefs\main')) -or -not (Test-Path (Join-Path $Dump 'romfs\Data'))) {
    Fail "$Dump does not hold exefs\main and romfs\Data"
}
Say "dump: $Dump"

# 3. tools
$py = Join-Path $Bin 'python\tools\python.exe'
if (-not (Test-Path $py)) {
    $pkg = Join-Path $Bin 'python.3.13.12.zip'
    Get-Pinned $PythonUrl $PythonSha $pkg
    Expand-Archive -Path $pkg -DestinationPath (Join-Path $Bin 'python') -Force
}
$lock = Join-Path $PSScriptRoot 'requirements.lock'
$stamp = Join-Path $Bin 'python\requirements.installed'
if (-not (Test-Path $stamp) -or ((Get-FileHash $stamp).Hash -ne (Get-FileHash $lock).Hash)) {
    Say 'installing the Python packages (setup\requirements.lock)'
    & $py -m pip install --disable-pip-version-check --no-warn-script-location -q -r $lock
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed' }
    Copy-Item $lock $stamp -Force
}
$ripper = Join-Path $Bin 'AssetRipper\AssetRipper.GUI.Free.exe'
if (-not (Test-Path $ripper)) {
    $zip = Join-Path $Bin 'AssetRipper_win_x64_1.3.14.zip'
    Get-Pinned $RipperUrl $RipperSha $zip
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Bin 'AssetRipper') -Force
}
Say "tools ready: Python 3.13.12, AssetRipper 1.3.14"

# 4. Wwise
if (-not $Wwise) { $Wwise = Join-Path $Work 'WwiseIntegration' }
$wver = Join-Path $Wwise 'Assets\Wwise\Version.txt'
function Wwise-Ready { (Test-Path $wver) -and ((Get-Content $wver -Raw) -match '2019\.2\.5 Build 7349') }
if (-not (Wwise-Ready)) {
    if (-not (Test-Path (Join-Path $Wwise 'ProjectSettings'))) {
        Say "creating an empty Unity project for the Wwise integration: $Wwise (a Unity window opens and closes)"
        for ($i = 0; $i -lt 3 -and -not (Test-Path (Join-Path $Wwise 'ProjectSettings')); $i++) {
            Start-Process -FilePath $Unity -ArgumentList @('-quit', '-createProject', "`"$Wwise`"", '-logFile', "`"$(Join-Path $Work 'wwise_create_project.log')`"") -Wait
        }
        if (-not (Test-Path (Join-Path $Wwise 'ProjectSettings'))) { Fail "Unity could not create $Wwise (see SetupWork\wwise_create_project.log)" }
    }
    Write-Host ''
    Say 'ONE MANUAL STEP - Wwise (free Audiokinetic account):'
    Say '  1. In the Audiokinetic Launcher, install Wwise 2019.2.5.7349: SDK (C++) with Microsoft > Windows (Visual Studio 2019).'
    Say "  2. Launcher > Unity > Browse for project > select:  $Wwise"
    Say '  3. Integrate Wwise into Project: version 2019.2.5.7349, platforms Windows + macOS (the Launcher requires macOS),'
    Say '     no "Unity Integration Extensions", no Addressables migration. If the Launcher reports a failure at its very'
    Say '     last step, that is fine - the setup only needs the files it copied.'
    Say 'Waiting for the integration to appear ... (Ctrl+C to stop; run setup.cmd again later to continue)'
    while (-not (Wwise-Ready)) { Start-Sleep -Seconds 5 }
}
Say "Wwise: $Wwise"

# 5. the setup proper
$argv = @('-I', (Join-Path $PSScriptRoot 'setup.py'), '--dump', $Dump, '--unity', $Unity, '--wwise', $Wwise, '--assetripper', $ripper,
          '--work', $Work)
if ($BatchMode) {
    $cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { New-Object psobject }
    $cfg | Add-Member -NotePropertyName unity_batchmode -NotePropertyValue $true -Force
    $cfg | ConvertTo-Json | Set-Content -Path $cfgPath -Encoding UTF8
}
Say 'running the setup (several hours; Unity windows open and close by themselves - do not close them)'
& $py @argv
if ($LASTEXITCODE -ne 0) { Fail "the setup stopped (exit $LASTEXITCODE) - see SetupWork\logs; run setup.cmd again to resume" }
Write-Host ''
Say 'DONE. Open this folder as a project in Unity Hub (Unity 2019.4.27f1), open Assets/Scenes/level0.unity and press Play.'
