<#
.SYNOPSIS
Checks (and, if you ask, installs) what Hippocampus needs on Windows. macOS and Linux: scripts/prereqs.sh.

.DESCRIPTION
READ THIS FIRST. By running this script with -Install or -Setup you confirm that you have read it (or trust where it came from)
and that you fully understand the risk: it can install software and change system settings on this computer (programs installed
with winget, Windows features such as WSL enabled, virtual machines created, a restart may be needed). Some of those changes are
hard to undo. It shows every command and asks y/N first, but you are responsible for what you approve. Read the commands before
you answer. Without -Install and -Setup it only LOOKS and changes nothing.

  .\scripts\prereqs.ps1                    check what is needed to RUN Hippocampus; prints what is missing; changes nothing
  .\scripts\prereqs.ps1 -Dev               also check what is needed to DEVELOP it (git, Python 3.12, node, and the optional test tools:
                                           playwright with Chromium, and fastembed)
  .\scripts\prereqs.ps1 -Install           after the report, ask y/N before EACH thing it would install or change
  .\scripts\prereqs.ps1 -Install -DryRun   show the exact commands it would run, without running them (changes nothing)
  .\scripts\prereqs.ps1 -Dev -Setup        create .venv and install the development tools, then offer the optional test tools (asks first)

Options: -Yes answers yes to every y/N question (it picks Docker as the engine); -Port N checks that another host port is free
(default 8090); -IUnderstandTheRisk gives the acknowledgement below without typing it (for non-interactive use).
Exit status: 0 = everything required is present, 1 = something required is missing or not working.

You need ONE container engine: Docker Desktop OR Podman. Either works; the launcher uses whichever it finds. Docker Desktop's licence
has conditions for larger companies (docker.com/pricing); Podman is free. Every program or system change is a separate y/N question that
shows the exact command first.

The Hippocampus launcher is a bash script. On Windows run it from Git Bash (Git for Windows) or from a WSL terminal:
    ./hippocampus up
This script has been checked for syntax and style but has NOT been run on a real Windows machine by its author: read what it prints.
.EXAMPLE
PS> .\scripts\prereqs.ps1 -Dev
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive console tool: colours and prompts are the point.')]
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Install,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$Setup,
    [switch]$IUnderstandTheRisk,
    [int]$Port = 8090
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
$script:AutoYes = [bool]$Yes   # the -Yes switch, read by Confirm-Action

if ($env:OS -ne 'Windows_NT') {
    Write-Error 'This script is for Windows. On macOS or Linux run scripts/prereqs.sh.'
    exit 2
}

# ---------------------------------------------------------------- the acknowledgement (before anything can change)
$AckPhrase = 'I HAVE READ THIS SCRIPT AND UNDERSTAND THE RISK'
if (($Install -or $Setup) -and -not $DryRun -and -not $IUnderstandTheRisk) {
    Write-Host ''
    Write-Host '  ==================================================================================================' -ForegroundColor Yellow
    Write-Host '   IMPORTANT: READ BEFORE CONTINUING' -ForegroundColor Yellow
    Write-Host '  ==================================================================================================' -ForegroundColor Yellow
    Write-Host '   This script can install software and change system settings on this computer: programs installed'
    Write-Host '   with winget, Windows features such as WSL enabled, virtual machines created, and a restart may be'
    Write-Host '   needed. It shows every command and asks y/N first, but YOU are responsible for what you approve,'
    Write-Host '   and some changes are hard to undo.'
    Write-Host ''
    Write-Host '   By continuing you confirm that:'
    Write-Host '     * you have read this script (scripts\prereqs.ps1) or you trust where it came from,'
    Write-Host '     * you fully understand that it can change your system,'
    Write-Host '     * you will read each command before answering y.'
    Write-Host ''
    Write-Host '   Type the following line exactly to continue (anything else exits and changes nothing):'
    Write-Host ''
    Write-Host "       $AckPhrase"
    Write-Host '  ==================================================================================================' -ForegroundColor Yellow
    $typed = Read-Host '  >'
    if ($typed -cne $AckPhrase) {
        Write-Host '  Not confirmed. Nothing was changed.'
        exit 1
    }
    Write-Host ''
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$script:Ok = @()
$script:Bad = @()
$script:Warnings = @()
$script:Notes = @()
$script:MissingItems = @()      # keys of single missing items
$script:MissingRequired = @()   # the subset without which Hippocampus cannot run or be developed

function Write-Ok([string]$Text) { $script:Ok += $Text; Write-Host "  [ok]      $Text" -ForegroundColor Green }
function Write-Bad([string]$Text, [string]$Hint = '') {
    $script:Bad += $Text
    Write-Host "  [MISSING] $Text" -ForegroundColor Red
    if ($Hint) { Write-Host "            $Hint" }
}
function Write-Warn([string]$Text, [string]$Hint = '') {
    $script:Warnings += $Text
    Write-Host "  [warn]    $Text" -ForegroundColor Yellow
    if ($Hint) { Write-Host "            $Hint" }
}
function Write-Info([string]$Text) { Write-Host "  [--]      $Text" }

function Test-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Get-VersionText([string]$Text) {
    $m = [regex]::Match($Text, '\d+(\.\d+)+')
    if ($m.Success) { return $m.Value }
    return ''
}

function Test-VersionAtLeast([string]$Have, [string]$Need) {
    if (-not $Have) { return $false }
    try { return ([version]$Have) -ge ([version]$Need) } catch { return $false }
}

function Confirm-Action([string]$Question) {
    if ($script:AutoYes) { Write-Host "$Question [y/N] y   (-Yes)"; return $true }
    $answer = Read-Host "$Question [y/N]"
    return ($answer -eq 'y' -or $answer -eq 'Y')
}

# ---------------------------------------------------------------- what can be installed
# Every installable thing is an "item": a label, why it is needed, and the exact command (empty Command = no automatic way).
$script:PythonLauncher = @()   # filled in by the Python check below (for pip-based items)

function Get-InstallItem([string]$Key) {
    $info = @{ Label = ''; Why = ''; Command = ''; Args = @(); Note = '' }
    switch ($Key) {
        'wsl' {
            $info.Label = 'WSL 2 (Windows Subsystem for Linux)'
            $info.Why = 'Docker Desktop and the Podman machine run their containers in a small Linux VM on WSL 2'
            $info.Command = 'wsl'; $info.Args = @('--install', '--no-distribution')
            $info.Note = 'A restart is usually needed afterwards. Run this script again after restarting.'
        }
        'docker-desktop' {
            $info.Label = 'Docker Desktop (the engine and compose together)'
            $info.Why = 'runs containers; includes the compose plugin'
            $info.Command = 'winget'; $info.Args = @('install', '-e', '--id', 'Docker.DockerDesktop', '--accept-source-agreements', '--accept-package-agreements')
            $info.Note = 'Start Docker Desktop once and accept its prompts. Its licence has conditions for larger companies (docker.com/pricing).'
        }
        'podman' {
            $info.Label = 'Podman'
            $info.Why = 'runs containers without a paid licence'
            $info.Command = 'winget'; $info.Args = @('install', '-e', '--id', 'RedHat.Podman', '--accept-source-agreements', '--accept-package-agreements')
        }
        'podman-compose' {
            $info.Label = 'podman-compose (a Python package)'
            $info.Why = "the compose provider that 'podman compose' needs"
            if ($script:PythonLauncher.Count -gt 0) {
                $info.Command = $script:PythonLauncher[0]
                $rest = @(); if ($script:PythonLauncher.Count -gt 1) { $rest = $script:PythonLauncher[1..($script:PythonLauncher.Count - 1)] }
                $info.Args = $rest + @('-m', 'pip', 'install', '--user', 'podman-compose')
            } else {
                $info.Note = 'Needs Python 3 first: install it (the Python item below, or https://www.python.org/downloads/windows/), then run: py -m pip install --user podman-compose'
            }
        }
        'podman-machine-init' {
            $info.Label = "create the Podman virtual machine ('podman machine init')"
            $info.Why = 'on Windows Podman runs containers inside a small Linux VM on WSL (downloads about 1 GB)'
            $info.Command = 'podman'; $info.Args = @('machine', 'init')
        }
        'podman-machine-start' {
            $info.Label = "start the Podman virtual machine ('podman machine start')"
            $info.Why = 'the engine must be running'
            $info.Command = 'podman'; $info.Args = @('machine', 'start')
        }
        'git' {
            $info.Label = 'Git for Windows (includes Git Bash)'
            $info.Why = 'the ./hippocampus launcher is a bash script, and Git Bash runs it; git is also needed to get and develop the code'
            $info.Command = 'winget'; $info.Args = @('install', '-e', '--id', 'Git.Git', '--accept-source-agreements', '--accept-package-agreements')
        }
        'python' {
            $info.Label = 'Python 3.12'
            $info.Why = 'to develop and test the code'
            $info.Command = 'winget'; $info.Args = @('install', '-e', '--id', 'Python.Python.3.12', '--accept-source-agreements', '--accept-package-agreements')
        }
        'node' {
            $info.Label = 'Node.js LTS (with npm and npx)'
            $info.Why = 'optional: runs the Markdown linter and the dashboard JavaScript syntax check'
            $info.Command = 'winget'; $info.Args = @('install', '-e', '--id', 'OpenJS.NodeJS.LTS', '--accept-source-agreements', '--accept-package-agreements')
        }
    }
    if ($info.Command -eq 'winget' -and -not (Test-Command 'winget')) {
        $info.Note = 'winget (App Installer) is not available on this PC: install this by hand from the vendor website. ' + $info.Note
        $info.Command = ''
    }
    return $info
}

# The items for each way of getting a container engine, in install order.
function Get-EngineItem([string]$Engine, [bool]$WslMissing) {
    $items = @()
    if ($WslMissing) { $items += 'wsl' }
    if ($Engine -eq 'docker') { $items += 'docker-desktop' }
    else { $items += @('podman', 'podman-compose', 'podman-machine-init', 'podman-machine-start') }
    return $items
}

function Show-Item([string]$Key) {
    $i = Get-InstallItem $Key
    Write-Host ''
    Write-Host "  * $($i.Label)"
    Write-Host "      why:     $($i.Why)"
    if ($i.Command) { Write-Host "      command: $($i.Command) $($i.Args -join ' ')" }
    else { Write-Host '      command: none for this system: install it by hand (see the note)' }
    if ($i.Note) { Write-Host "      note:    $($i.Note)" }
}

function Invoke-ItemInstall([string]$Key) {
    # Shows the item, asks y/N, runs it. Returns $true if it ran successfully.
    $i = Get-InstallItem $Key
    Show-Item $Key
    if (-not $i.Command) { return $false }
    if (Confirm-Action "    Install this: $($i.Label)?") {
        if ($DryRun) { Write-Host '      (dry run: not executed)'; return $true }
        & $i.Command @($i.Args)
        if ($LASTEXITCODE -eq 0) { return $true }
        Write-Host '      that command failed: see its output above' -ForegroundColor Yellow
        return $false
    }
    Write-Host '      skipped'
    return $false
}

# ---------------------------------------------------------------- the checks
$build = [System.Environment]::OSVersion.Version.Build
Write-Host "Hippocampus prerequisites (Windows, build $build)"
Write-Host ''
Write-Host 'To run Hippocampus: one container engine (Docker OR Podman) with compose'

# Windows version and virtualisation: both engines need WSL 2 or Hyper-V
if ($build -ge 19041) { Write-Ok "Windows build $build supports WSL 2" }
else { Write-Bad "Windows build $build is too old for WSL 2 (needs 19041 or newer)" 'Update Windows.' }

try {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    $cpu = Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop | Select-Object -First 1
    if ($cs.HypervisorPresent -or $cpu.VirtualizationFirmwareEnabled) { Write-Ok 'hardware virtualisation is available' }
    else { Write-Warn 'hardware virtualisation looks disabled' 'Turn on Intel VT-x / AMD-V (SVM) in the BIOS or UEFI settings; both engines need it.' }
} catch {
    Write-Warn 'could not read the virtualisation setting' 'Open Task Manager, Performance, CPU: "Virtualization" should say Enabled.'
}

$wslMissing = $true
if (Test-Command 'wsl') {
    $null = & wsl.exe --status 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Ok 'WSL is installed'; $wslMissing = $false }
    else { Write-Warn 'WSL is present but not set up' 'Needed by Docker Desktop (default backend) and the Podman machine.' }
} else {
    Write-Warn 'WSL is not installed' 'Needed by Docker Desktop (default backend) and the Podman machine.'
}

# Docker and Podman are checked separately and both are reported; ONE working engine is enough.
$dockerOk = $false
$podmanOk = $false
if (Test-Command 'docker') {
    $dockerVersion = Get-VersionText ((& docker --version 2>&1) | Out-String)
    $composeText = (& docker compose version 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0) {
        $null = & docker info 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Info "Docker $dockerVersion with compose $(Get-VersionText $composeText): installed and RUNNING"; $dockerOk = $true }
        else { Write-Info "Docker $dockerVersion with compose $(Get-VersionText $composeText): installed but the engine is NOT running (start Docker Desktop)" }
    } else { Write-Info "Docker ${dockerVersion}: installed, but 'docker compose' (the v2 plugin) is missing: update Docker Desktop" }
} else { Write-Info 'Docker: not installed' }

if (Test-Command 'podman') {
    $podmanVersion = Get-VersionText ((& podman --version 2>&1) | Out-String)
    $null = & podman compose version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $null = & podman info 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Info "Podman $podmanVersion with compose: installed and RUNNING"; $podmanOk = $true }
        else { Write-Info "Podman $podmanVersion with compose: installed but not running (podman machine start)" }
    } else { Write-Info "Podman ${podmanVersion}: installed, but 'podman compose' has no compose provider (pip install podman-compose, or install docker-compose)" }
} else { Write-Info 'Podman: not installed' }

$needEngine = $false
$engine = ''
if ($dockerOk) { $engine = 'docker' } elseif ($podmanOk) { $engine = 'podman' }
if ($engine) {
    Write-Ok "a working container engine with compose: $engine (Docker is used if both are present)"
    $mem = (& $engine info --format '{{.MemTotal}}' 2>$null | Out-String).Trim()
    if ($mem -match '^\d+$') {
        $gb = [math]::Floor([double]$mem / 1GB)
        if ($gb -ge 2) { Write-Ok "the engine has about $gb GB of memory" }
        else { Write-Warn "the engine has only about $gb GB of memory" 'Hippocampus wants at least 2 GB: raise it in Docker Desktop (Settings, Resources) or in .wslconfig.' }
    }
} else {
    $needEngine = $true
    if ((Test-Command 'docker') -or (Test-Command 'podman')) {
        Write-Bad 'no WORKING container engine: one is installed but not usable (see the lines above)' 'Start it (Docker Desktop, or "podman machine start"), or install the missing compose part below.'
    } else {
        Write-Bad 'no container engine: you need Docker Desktop OR Podman' 'Either one works.'
    }
}

Write-Host ''
Write-Host 'Other requirements'

# bash for the launcher: Git Bash or a WSL distribution
$gitBash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
$hasGitBash = Test-Path -LiteralPath $gitBash
$hasWslDistro = $false
if (Test-Command 'wsl') {
    $distros = (& wsl.exe -l -q 2>$null | Out-String) -replace "`0", ''
    $hasWslDistro = [bool]($distros.Trim())
}
if ($hasGitBash) { Write-Ok "Git Bash is available ($gitBash): run ./hippocampus from it" }
elseif ($hasWslDistro) { Write-Ok 'a WSL distribution is available: run ./hippocampus from its terminal' }
else {
    Write-Bad 'no bash for the ./hippocampus launcher (neither Git for Windows nor a WSL distribution)' 'Install Git for Windows (it includes Git Bash): https://git-scm.com/download/win'
    $script:MissingItems += 'git'
    $script:MissingRequired += 'git'
}
if ($hasWslDistro -and -not $engine) { $script:Notes += 'In WSL, turn on Docker Desktop WSL integration for your distribution: Docker Desktop, Settings, Resources, WSL integration.' }

if (Test-Command 'curl.exe') { Write-Ok 'curl.exe is available' }
else { Write-Warn 'curl.exe was not found' 'The launcher falls back to python3 if present; Git Bash includes curl.' }

try {
    $drive = (Get-Item -LiteralPath $RepoRoot).PSDrive
    $freeGb = [math]::Floor($drive.Free / 1GB)
    if ($freeGb -ge 3) { Write-Ok "free disk space on $($drive.Name): about $freeGb GB" }
    else { Write-Warn "free disk space on $($drive.Name): about $freeGb GB" 'The two images need about 1.2 GB (2 GB with the browser engine) plus your data. Containers store them on their own disk image, usually on C:.' }
} catch {
    Write-Warn 'could not read the free disk space'
}

$listening = $null
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
}
if ($listening) { Write-Warn "port $Port is already in use on this machine" "Set HIPPO_PORT in hippocampus.env to a free port, or stop the program using $Port." }
else { Write-Ok "port $Port is free" }

try {
    $null = Invoke-WebRequest -Uri 'https://registry-1.docker.io/v2/' -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    Write-Ok 'can reach the container registry (needed once, for the first build)'
} catch {
    $status = $null
    if ($_.Exception.PSObject.Properties.Match('Response').Count -gt 0 -and $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    if ($status -eq 401) { Write-Ok 'can reach the container registry (needed once, for the first build)' }
    else { Write-Warn 'could not reach the container registry (registry-1.docker.io)' 'The first build downloads images and the embedding model. Check your connection, VPN or proxy.' }
}

# Python is looked for in every run (the optional podman-compose package needs it), and is required only for development.
$pythonCmd = @()
$pythonVersion = ''
if (Test-Command 'py') {
    foreach ($v in '3.13', '3.12') {
        $out = (& py "-$v" --version 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and (Test-VersionAtLeast (Get-VersionText $out) '3.12')) { $pythonCmd = @('py', "-$v"); $pythonVersion = Get-VersionText $out; break }
    }
}
if ($pythonCmd.Count -eq 0 -and (Test-Command 'python')) {
    # "python" can be a Microsoft Store stub that prints nothing useful, so only trust it if it reports a version
    $out = (& python --version 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0 -and (Test-VersionAtLeast (Get-VersionText $out) '3.12')) { $pythonCmd = @('python'); $pythonVersion = Get-VersionText $out }
}
$script:PythonLauncher = $pythonCmd

if ($Dev) {
    Write-Host ''
    Write-Host 'To develop Hippocampus'

    if (Test-Command 'git') { Write-Ok "git $(Get-VersionText ((& git --version) | Out-String))" }
    else {
        Write-Bad 'git is missing' 'https://git-scm.com/download/win'
        if ($script:MissingItems -notcontains 'git') { $script:MissingItems += 'git' }
        if ($script:MissingRequired -notcontains 'git') { $script:MissingRequired += 'git' }
    }

    if ($pythonCmd.Count -gt 0) { Write-Ok "python $pythonVersion ($($pythonCmd -join ' '))" }
    else {
        Write-Bad 'Python 3.12 or newer is required' 'https://www.python.org/downloads/windows/  (tick "Add python.exe to PATH" in the installer)'
        $script:MissingItems += 'python'
        $script:MissingRequired += 'python'
    }

    if ((Test-Command 'node') -and (Test-Command 'npx')) {
        $nodeVersion = Get-VersionText ((& node --version) | Out-String)
        if (Test-VersionAtLeast $nodeVersion '18.0') { Write-Ok "node $nodeVersion and npx (for the Markdown linter and the dashboard JavaScript check)" }
        else { Write-Warn "node $nodeVersion is older than 18" 'The Markdown linter needs node 18 or newer.'; $script:MissingItems += 'node' }
    } else {
        Write-Warn 'node and npx are not installed' 'Optional: only the Markdown linter and the dashboard JavaScript syntax check use them.'
        $script:MissingItems += 'node'
    }

    $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) { Write-Ok 'a virtualenv exists (.venv)' }
    else { Write-Warn 'no .venv yet' 'Create it with: .\scripts\prereqs.ps1 -Dev -Setup   (or: py -3.12 -m venv .venv ; .venv\Scripts\pip install -e ".[db,api,mcp,dev]")' }

    # Optional test tools: without them two tests are skipped (the run report says so), and the dashboard tests cannot run.
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -c 'import playwright' 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'playwright is not installed: the browser-crawl test and the dashboard (e2e) tests will be skipped' "Optional. .\scripts\prereqs.ps1 -Dev -Setup offers to install it (the 'browser' extra) and Chromium (about 150 MB)."
        } else {
            & $venvPython -c 'import os; from playwright.sync_api import sync_playwright as s; p = s().start(); ok = os.path.exists(p.chromium.executable_path); p.stop(); raise SystemExit(0 if ok else 1)' 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Warn 'Chromium is not installed for playwright: the browser-crawl test and the dashboard (e2e) tests will be skipped' 'Optional. Install it with: .venv\Scripts\playwright.exe install chromium (about 150 MB).' }
            else { Write-Ok 'playwright and Chromium (browser-crawl test and dashboard e2e tests can run)' }
        }
        & $venvPython -c 'import fastembed' 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Warn 'fastembed is not installed: the embedding model test cannot run' "Optional. .\scripts\prereqs.ps1 -Dev -Setup offers to install it (the 'embed' extra)." }
        else { Write-Ok 'fastembed (the embedding model test can run)' }
    }
    $script:Notes += 'The embedding model test downloads the model (about 130 MB) and only runs with HIPPO_RUN_MODEL_TESTS=1 (set it in the environment, then run pytest on tests/integration/test_embedder_model.py).'
}

# ---------------------------------------------------------------- what needs to be installed
Write-Host ''
Write-Host '================ what needs to be installed ================'
if (-not $needEngine -and $script:MissingItems.Count -eq 0) {
    Write-Host '  Nothing. Everything required is already present.'
} else {
    if ($needEngine) {
        Write-Host ''
        Write-Host '  REQUIRED: a container engine. You need ONE of these two (either works; you do not need both):'
        foreach ($e in 'docker', 'podman') {
            Write-Host ''
            if ($e -eq 'docker') { Write-Host '  Option A: Docker Desktop' } else { Write-Host '  Option B: Podman' }
            foreach ($k in (Get-EngineItem $e $wslMissing)) { Show-Item $k }
        }
    }
    foreach ($k in $script:MissingItems) {
        $req = 'optional'
        if ($script:MissingRequired -contains $k) { $req = 'REQUIRED' }
        Write-Host ''
        Write-Host "  ${req}:"
        Show-Item $k
    }
    Write-Host ''
    if ($Install) {
        Write-Host '  You will now be asked y/N before EACH item above. Anything you answer N to is not installed.'
    } else {
        Write-Host '  Nothing was installed. To be asked about each item, run this again with -Install'
        Write-Host '  (add -DryRun first to see exactly what would run; no acknowledgement is needed for a dry run).'
    }
}

# ---------------------------------------------------------------- the install pass (only with -Install)
if ($Install -and ($needEngine -or $script:MissingItems.Count -gt 0)) {
    if ($needEngine) {
        Write-Host ''
        Write-Host '---- Container engine ----'
        $engineDone = $false
        foreach ($e in 'docker', 'podman') {
            if ($engineDone) { break }
            Write-Host ''
            if (Confirm-Action "Do you want to install $e as your container engine?") {
                foreach ($k in (Get-EngineItem $e $wslMissing)) { $null = Invoke-ItemInstall $k }
                $engineDone = $true
            } else {
                Write-Host "  not installing $e"
            }
        }
        if (-not $engineDone) { Write-Host '  No engine installed. Install Docker Desktop or Podman by hand, then run this script again.' }
    }
    foreach ($k in $script:MissingItems) {
        Write-Host ''
        $null = Invoke-ItemInstall $k
    }
}

# ---------------------------------------------------------------- development environment
if ($Dev -and $Setup -and $pythonCmd.Count -gt 0) {
    Write-Host ''
    Write-Host '---- Development environment ----'
    Write-Host '  * create .venv and install the project with its development tools'
    Write-Host "      command: $($pythonCmd -join ' ') -m venv .venv ; .venv\Scripts\pip.exe install -e `".[db,api,mcp,dev]`"   (about 200 MB, needs internet)"
    if (Confirm-Action '    Do this?') {
        if ($DryRun) { Write-Host '      (dry run: not executed)' }
        else {
            $exe = $pythonCmd[0]
            $extra = @(); if ($pythonCmd.Count -gt 1) { $extra = $pythonCmd[1..($pythonCmd.Count - 1)] }
            & $exe @($extra + @('-m', 'venv', '.venv'))
            if ($LASTEXITCODE -eq 0) {
                & '.venv\Scripts\pip.exe' install -q -e '.[db,api,mcp,dev]'
                if ($LASTEXITCODE -eq 0) { Write-Host '      done. Next: .venv\Scripts\python.exe scripts\check.py --no-db' }
            }
        }
    } else { Write-Host '      skipped' }

    # Optional test tools. Each is its own question; nothing here is needed to run Hippocampus.
    $venvPip = '.venv\Scripts\pip.exe'
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        Write-Host '  * (optional) browser tests: the playwright package and the Chromium browser'
        Write-Host "      command: $venvPip install -e `".[browser]`" ; .venv\Scripts\playwright.exe install chromium   (about 150 MB, needs internet)"
        if (Confirm-Action '    Do this?') {
            if ($DryRun) { Write-Host '      (dry run: not executed)' }
            else {
                & $venvPip install -q -e '.[browser]'
                if ($LASTEXITCODE -eq 0) { & '.venv\Scripts\playwright.exe' install chromium }
            }
        } else { Write-Host '      skipped' }
        Write-Host '  * (optional) embedding model test: the fastembed package (the model itself downloads when the test runs)'
        Write-Host "      command: $venvPip install -e `".[embed]`"   (needs internet)"
        if (Confirm-Action '    Do this?') {
            if ($DryRun) { Write-Host '      (dry run: not executed)' }
            else { & $venvPip install -q -e '.[embed]' }
        } else { Write-Host '      skipped' }
    }
}

# ---------------------------------------------------------------- summary
Write-Host ''
Write-Host '================ summary ================'
Write-Host "  ok: $($script:Ok.Count)   warnings: $($script:Warnings.Count)   missing: $($script:Bad.Count)"
foreach ($n in $script:Notes) { Write-Host "  note: $n" }
if ($script:Bad.Count -gt 0) {
    Write-Host '  Missing:'
    foreach ($b in $script:Bad) { Write-Host "    - $b" }
    if ($Install -and -not $DryRun) { Write-Host '  If you installed something, open a NEW PowerShell window and run this script again to confirm it works (PATH changes only apply to new windows; a restart may be needed first).' }
    Write-Host 'RESULT: not ready'
    exit 1
}
Write-Host 'RESULT: ready. From Git Bash or WSL start Hippocampus with:  ./hippocampus up'
exit 0
