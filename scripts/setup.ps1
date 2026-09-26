<#
.SYNOPSIS
    Reproducible setup for Guitaroids on Windows. PowerShell equivalent of setup.sh.

.DESCRIPTION
    Installs dependencies in the one order that works. This is the ONLY supported
    install path -- see requirements.txt for why a bare `pip install -r
    requirements.txt` is not equivalent.

    PowerShell equivalent of scripts/setup.sh.
    KEEP THE TWO IN SYNC -- tests/test_setup_scripts.py asserts they agree on the
    critical pins (opencv version, the model URL, the soundfont URL).

    Three things are not optional and cannot be fixed by editing a requirements file:

      1. OpenCV ordering. mediapipe hard-requires the GUI build of OpenCV, whose
         bundled Qt plugins under cv2/qt/plugins hijack QT_PLUGIN_PATH and break
         PySide6 with 'Could not load the Qt platform plugin "xcb"'. pip treats the
         GUI and headless builds as unrelated distributions, so both get installed.
         Both write the same cv2/ directory, so the GUI build must be removed
         BEFORE headless is installed. The reverse order deletes the headless
         files while leaving its dist-info, and pip then reports "already
         satisfied" and restores nothing, leaving 'import cv2' broken.

      2. tinysoundfont needs --no-deps. Its only dependency is pyaudio, which has
         no Windows wheel in any release and cannot be built without the PortAudio
         SDK. pyaudio is a lazy import used only for real-time playback, and we
         render offline, so --no-deps costs nothing. Locked in by
         tests/test_audio_deps.py.

      3. The model and the soundfont are not Python packages. They are fetched,
         and gitignored.

    Only the soundfont step is allowed to fail: the numpy Karplus-Strong synth
    needs nothing at all and takes over if it is absent.

.PARAMETER Python
    Interpreter used to create the venv. Must be Python 3.12.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -Python C:\Python312\python.exe
#>

[CmdletBinding()]
param(
    [string] $Python = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Windows uses Scripts/, not bin/. Detect whichever this platform created.
function Get-VenvPython {
    $candidates = @(
        (Join-Path $Root '.venv\Scripts\python.exe'),
        (Join-Path $Root '.venv/bin/python')
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

# $ErrorActionPreference does not catch a non-zero exit from a native command, so
# every external call goes through this and throws instead.
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string] $Exe,
        [Parameter()][string[]] $NativeArgs = @(),
        [Parameter()][switch] $AllowFailure
    )
    & $Exe @NativeArgs
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFailure) { return $false }
        throw "'$Exe $($NativeArgs -join ' ')' failed with exit code $LASTEXITCODE"
    }
    return $true
}

function Write-Step { param([string] $Message) Write-Host "==> $Message" }
function Write-Note { param([string] $Message) Write-Host "    $Message" }

Write-Host "Guitaroids setup (PowerShell)"
Write-Host "Repository: $Root"
Write-Host ""

# --- interpreter ----------------------------------------------------------------

$Py = Get-VenvPython
if (-not $Py) {
    Write-Step "Creating venv (override with -Python)"
    Invoke-Native -Exe $Python -NativeArgs @('-m', 'venv', '.venv')
    $Py = Get-VenvPython
    if (-not $Py) { throw "venv creation failed: .venv\Scripts\python.exe not found" }
}

Invoke-Native -Exe $Py -NativeArgs @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet') | Out-Null

Write-Step "Checking the interpreter"
$VersionText = (& $Py --version) -join ''
Write-Note $VersionText
$PyVer = (& $Py -c 'import sys; print("%d.%d" % sys.version_info[:2])') -join ''
switch ($PyVer) {
    '3.12' { Write-Note 'known-good: every dependency including tinysoundfont has a wheel' }
    '3.10' { Write-Note 'known-good: wheels for everything; numpy is capped at 2.2.6' }
    '3.14' { Write-Note 'WARNING: no tinysoundfont wheel, so it builds from source,' }
    default { Write-Note 'WARNING: untested version; tinysoundfont may fail to build.' }
}
if ($PyVer -ne '3.12' -and $PyVer -ne '3.10') {
    Write-Note 'WARNING: that can need a C++ toolchain and Python dev headers.'
}

# --- dependencies ---------------------------------------------------------------

Write-Step 'Installing pinned dependencies'
Invoke-Native -Exe $Py -NativeArgs @('-m', 'pip', 'install', '-r', 'requirements-dev.txt', '--quiet') | Out-Null

# Both OpenCV builds install into the same cv2/ directory and clobber each other.
# Removing only the GUI build leaves headless's dist-info behind with its files
# deleted, and pip then says "already satisfied" and reinstalls nothing. So remove
# BOTH, then install headless cleanly.
# The version is written literally rather than into a variable so that
# tests/test_setup_scripts.py can compare it against setup.sh.
$GuiVersion = ''
$GuiShow = & $Py -m pip show opencv-contrib-python 2>$null
if ($LASTEXITCODE -eq 0) {
    $GuiVersion = (($GuiShow | Select-String '^Version:').ToString() -replace '^Version:\s*', '').Trim()
}
Invoke-Native -Exe $Py -NativeArgs @(
    '-m', 'pip', 'uninstall', '-y',
    'opencv-contrib-python', 'opencv-contrib-python-headless', '--quiet'
) -AllowFailure | Out-Null

$guiNote = if ($GuiVersion) { "GUI build $GuiVersion removed" } else { 'GUI build (absent) removed' }
Write-Step "Installing headless OpenCV build ($guiNote)"
Invoke-Native -Exe $Py -NativeArgs @(
    '-m', 'pip', 'install', 'opencv-contrib-python-headless==5.0.0.93', '--quiet'
) | Out-Null

Write-Step 'Installing optional packages that need --no-deps (requirements-optional.txt)'
if (Invoke-Native -Exe $Py -NativeArgs @(
        '-m', 'pip', 'install', '-r', 'requirements-optional.txt', '--no-deps', '--quiet'
    ) -AllowFailure) {
    Write-Note 'ok - sampled soundfonts available'
}
else {
    Write-Note 'FAILED - continuing. The numpy Karplus-Strong synth is the fallback,'
    Write-Note 'so audio still renders without it. See requirements-optional.txt.'
}

# --- assets ---------------------------------------------------------------------

# TLS 1.2 for Windows PowerShell 5.1; a no-op on PowerShell 7+.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
}
catch { Write-Note 'could not raise the TLS version; download may fail on PowerShell 5.1' }

function Get-Asset {
    param(
        [Parameter(Mandatory)][string] $Url,
        [Parameter(Mandatory)][string] $Dest,
        [Parameter()][string] $Label
    )
    if (Test-Path $Dest) {
        Write-Step "Already present: $Dest ($((Get-Item $Dest).Length) bytes)"
        return $true
    }
    Write-Step "Downloading $Label"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
    try {
        Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dest) | Out-Null
        Move-Item -Force $tmp $Dest
        Write-Note "Saved $Dest ($((Get-Item $Dest).Length) bytes)"
        return $true
    }
    catch {
        Write-Note "download failed: $($_.Exception.Message)"
        return $false
    }
    finally {
        if (Test-Path $tmp) { Remove-Item -Force $tmp -ErrorAction SilentlyContinue }
    }
}

$ModelUrl = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'
Get-Asset -Url $ModelUrl -Dest (Join-Path $Root 'assets\hand_landmarker.task') `
    -Label 'mediapipe hand landmarker model' | Out-Null

# --- soundfont (optional) -------------------------------------------------------

Write-Step 'Fetching a soundfont (optional, used by tinysoundfont)'

$existing = @('assets\soundfont.sf2', 'assets\soundfont.sf3') |
    Where-Object { Test-Path (Join-Path $Root $_) } | Select-Object -First 1

if ($existing) {
    Write-Note "Already present: $existing"
}
else {
    $DebUrl = 'http://deb.debian.org/debian/pool/main/f/fluidr3mono-gm-soundfont/fluidr3mono-gm-soundfont_2.315-7_all.deb'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("guitaroids-sf-" + [IO.Path]::GetRandomFileName())
    $extract = Join-Path ([IO.Path]::GetTempPath()) ("guitaroids-sfx-" + [IO.Path]::GetRandomFileName())

    try {
        Invoke-WebRequest -Uri $DebUrl -OutFile $tmp -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $extract | Out-Null

        # A .deb is an ar archive: '!<arch>' containing data.tar.xz. dpkg-deb is used
        # where present; otherwise the Windows bsdtar reads the ar layer directly and
        # a second pass unpacks the inner tar.
        $unpacked = $false
        if (Get-Command 'dpkg-deb' -ErrorAction SilentlyContinue) {
            if (Invoke-Native -Exe 'dpkg-deb' -NativeArgs @('-x', $tmp, $extract) -AllowFailure) {
                $unpacked = $true
                Write-Note 'extracted with dpkg-deb'
            }
        }
        if (-not $unpacked -and (Get-Command 'tar' -ErrorAction SilentlyContinue)) {
            if (Invoke-Native -Exe 'tar' -NativeArgs @('-xf', $tmp, '-C', $extract) -AllowFailure) {
                $inner = Get-ChildItem -Path $extract -Filter 'data.tar.*' -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($inner) {
                    if (Invoke-Native -Exe 'tar' -NativeArgs @('-xf', $inner.FullName, '-C', $extract) -AllowFailure) {
                        $unpacked = $true
                        Write-Note 'extracted with tar (ar, then data.tar.*)'
                    }
                }
                else {
                    Write-Note 'ar layer unpacked but no data.tar.* found'
                }
            }
        }

        if (-not $unpacked) {
            Write-Note 'could not extract the soundfont package.'
            Write-Note 'The numpy Karplus-Strong synth will be used instead.'
            Write-Note 'To supply one manually, set GUITAROIDS_SOUNDFONT or drop an'
            Write-Note '.sf2/.sf3 into assets/.'
        }
        else {
            $sf = Get-ChildItem -Path $extract -Include '*.sf2', '*.sf3' -Recurse -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($sf) {
                $ext = $sf.Extension.TrimStart('.').ToLower()
                $dest = Join-Path $Root "assets\soundfont.$ext"
                New-Item -ItemType Directory -Force -Path (Join-Path $Root 'assets') | Out-Null
                Copy-Item -Force $sf.FullName $dest
                Write-Note "Saved assets\soundfont.$ext ($((Get-Item $dest).Length) bytes)"
                Write-Note 'FluidR3 (c) Frank Wen, MIT licence. See assets/ATTRIBUTION-soundfont.md'
            }
            else {
                Write-Note 'no .sf2/.sf3 inside the package; numpy synth will be used'
            }
        }
    }
    catch {
        Write-Note "soundfont fetch failed: $($_.Exception.Message)"
        Write-Note 'The numpy Karplus-Strong synth will be used instead.'
    }
    finally {
        foreach ($p in @($tmp, $extract)) {
            if ($p -and (Test-Path $p)) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
        }
    }
}

# --- gate -----------------------------------------------------------------------

Write-Host ""
Write-Step 'Verifying the M0 gate'
Invoke-Native -Exe $Py -NativeArgs @('-m', 'pytest', 'tests/test_m0_window.py', '-q') | Out-Null

Write-Host ""
Write-Host "Setup complete. See DESIGN.md 7 for track choice and audio synthesis."
Write-Host "Two gotchas that have bitten this project:"
Write-Host "  - use this script, not 'pip install -r requirements.txt' (opencv order)"
Write-Host "  - tinysoundfont needs --no-deps; pyaudio cannot be installed here"
