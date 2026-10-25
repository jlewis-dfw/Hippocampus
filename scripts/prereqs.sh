#!/usr/bin/env bash
# READ THIS FIRST. By running this script with --install or --setup you confirm that you have read it (or trust where it came from)
# and that you fully understand the risk: it can install software and change system settings on this computer (packages installed
# with sudo, services enabled, group membership changed, virtual machines created). Some of those changes are hard to undo.
# It shows every command and asks y/N first, but you are responsible for what you approve. Read the commands before you answer.
# Without --install and --setup it only LOOKS and changes nothing.
#
# Checks (and, if you ask, installs) what Hippocampus needs on macOS or Linux. Windows: scripts/prereqs.ps1.
#
#   scripts/prereqs.sh                       check what is needed to RUN Hippocampus; prints what is missing; changes nothing
#   scripts/prereqs.sh --dev                 also check what is needed to DEVELOP it (git, Python 3.12, node, and the optional test tools:
#                                            playwright with Chromium, and fastembed)
#   scripts/prereqs.sh --install             after the report, ask y/N before EACH thing it would install or change
#   scripts/prereqs.sh --install --dry-run   show the exact commands it would run, without running them (changes nothing)
#   scripts/prereqs.sh --dev --setup         create .venv and install the development tools, then offer the optional test tools (asks first)
#
# Options: --yes answers "yes" to every y/N question (it picks Docker as the engine); --port N checks that a different host port is
# free (default 8090); --i-understand-the-risk gives the acknowledgement below without typing it (for non-interactive use).
# Exit status: 0 = everything required is present, 1 = something required is missing or not working.
#
# You need ONE container engine: Docker (with the compose plugin) OR Podman (with a compose provider). Either works; the launcher
# uses whichever it finds. Every package or system change is a separate y/N question that shows the exact command first.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

DEV=0; INSTALL=0; DRY=0; YES=0; SETUP=0; PORT=8090; ACK=0
while (( $# )); do
  case "$1" in
    --dev) DEV=1 ;; --install) INSTALL=1 ;; --dry-run) DRY=1 ;; --yes) YES=1 ;; --setup) SETUP=1 ;;
    --i-understand-the-risk) ACK=1 ;;
    --port) PORT=${2:?--port needs a number}; shift ;;
    -h|--help) awk 'NR>1 && /^set -uo/ {exit} NR>1 {sub(/^# ?/, ""); print}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------- the acknowledgement (before anything can change)
ACK_PHRASE="I HAVE READ THIS SCRIPT AND UNDERSTAND THE RISK"
if (( (INSTALL || SETUP) && ! DRY && ! ACK )); then
  cat <<EOF

  ==================================================================================================
   IMPORTANT: READ BEFORE CONTINUING
  ==================================================================================================
   This script can install software and change system settings on this computer: packages installed
   with sudo, services enabled, your account added to a group, virtual machines created.
   It shows every command and asks y/N first, but YOU are responsible for what you approve, and
   some changes are hard to undo.

   By continuing you confirm that:
     * you have read this script (scripts/prereqs.sh) or you trust where it came from,
     * you fully understand that it can change your system,
     * you will read each command before answering y.

   Type the following line exactly to continue (anything else exits and changes nothing):

       $ACK_PHRASE
  ==================================================================================================
EOF
  printf '  > '; read -r typed
  if [[ $typed != "$ACK_PHRASE" ]]; then echo "  Not confirmed. Nothing was changed."; exit 1; fi
  echo
fi

# ---------------------------------------------------------------- platform
OS=$(uname -s)
PKG=""            # brew | apt | dnf | pacman | zypper | ""
DISTRO=""
IS_WSL=0
if [[ $OS == Darwin ]]; then
  PKG=brew
elif [[ $OS == Linux ]]; then
  # shellcheck source=/dev/null
  [[ -r /etc/os-release ]] && . /etc/os-release && DISTRO="${ID:-}"
  grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=1
  if command -v apt-get >/dev/null 2>&1; then PKG=apt
  elif command -v dnf >/dev/null 2>&1; then PKG=dnf
  elif command -v pacman >/dev/null 2>&1; then PKG=pacman
  elif command -v zypper >/dev/null 2>&1; then PKG=zypper; fi
else
  echo "This script supports macOS and Linux. On Windows run scripts/prereqs.ps1 in PowerShell." >&2
  exit 2
fi

OK=(); BAD=(); WARN=(); NOTES=()
ok()   { OK+=("$1"); printf '  [ok]      %s\n' "$1"; }
bad()  { BAD+=("$1"); printf '  [MISSING] %s\n' "$1"; [[ -n ${2:-} ]] && printf '            %s\n' "$2"; }
warn() { WARN+=("$1"); printf '  [warn]    %s\n' "$1"; [[ -n ${2:-} ]] && printf '            %s\n' "$2"; }
info() { printf '  [--]      %s\n' "$1"; }

have() { command -v "$1" >/dev/null 2>&1; }
ver_ge() { [[ $(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1) == "$2" ]]; }   # is version $1 >= $2
ver_of() { grep -Eo '[0-9]+(\.[0-9]+)+' | head -n1; }

# ---------------------------------------------------------------- what can be installed
# Every installable thing is an "item" with a key. item_info sets ITEM_LABEL (what it is), ITEM_WHY (why it is needed), ITEM_CMD
# (the exact command, as an array) and ITEM_NOTE. An empty ITEM_CMD means there is no automatic way on this system.
item_info() {
  ITEM_LABEL=""; ITEM_WHY=""; ITEM_CMD=(); ITEM_NOTE=""
  case "$1" in
    docker-desktop) ITEM_LABEL="Docker Desktop"; ITEM_WHY="runs containers (engine and compose together); macOS app" ;;
    docker-engine)  ITEM_LABEL="Docker Engine"; ITEM_WHY="runs containers" ;;
    docker-compose) ITEM_LABEL="Docker Compose plugin"; ITEM_WHY="lets 'docker compose' start the three services from compose.yaml" ;;
    docker-service) ITEM_LABEL="start the Docker service now and at every boot"; ITEM_WHY="the engine must be running" ;;
    docker-group)   ITEM_LABEL="add you ($USER) to the docker group"; ITEM_WHY="so you can use docker without sudo (log out and in afterwards); this gives the account root-equivalent control through containers" ;;
    podman)         ITEM_LABEL="Podman"; ITEM_WHY="runs containers without a background service" ;;
    podman-compose) ITEM_LABEL="podman-compose"; ITEM_WHY="the compose provider that 'podman compose' needs" ;;
    podman-machine-init)  ITEM_LABEL="create the Podman virtual machine ('podman machine init')"; ITEM_WHY="on macOS Podman runs containers inside a small Linux VM (downloads about 1 GB)" ;;
    podman-machine-start) ITEM_LABEL="start the Podman virtual machine ('podman machine start')"; ITEM_WHY="the engine must be running" ;;
    curl)           ITEM_LABEL="curl"; ITEM_WHY="the launcher uses it to wait for the service (python3 would also do)" ;;
    git)            ITEM_LABEL="git"; ITEM_WHY="to get the code and to develop it" ;;
    python)         ITEM_LABEL="Python 3.12 or newer (with venv and pip)"; ITEM_WHY="to develop and test the code" ;;
    node)           ITEM_LABEL="Node.js 18 or newer (with npm and npx)"; ITEM_WHY="optional: runs the Markdown linter and the dashboard JavaScript syntax check" ;;
  esac
  case "$1:$PKG" in
    docker-desktop:brew)       ITEM_CMD=(brew install --cask docker); ITEM_NOTE="Start Docker Desktop once from Applications and accept its prompts. (A licence-free alternative is Podman, or Colima: brew install colima docker docker-compose.)" ;;
    docker-engine:apt)         ITEM_CMD=(sudo apt-get install -y docker.io) ;;
    docker-engine:dnf)         ITEM_CMD=(sudo dnf install -y moby-engine) ;;
    docker-engine:pacman)      ITEM_CMD=(sudo pacman -S --noconfirm docker) ;;
    docker-engine:zypper)      ITEM_CMD=(sudo zypper install -y docker) ;;
    docker-compose:apt)        ITEM_CMD=(sudo apt-get install -y docker-compose-v2); ITEM_NOTE="Package name for Ubuntu 22.04+ and recent Debian. On other versions see https://docs.docker.com/engine/install/" ;;
    docker-compose:dnf)        ITEM_CMD=(sudo dnf install -y docker-compose) ;;
    docker-compose:pacman)     ITEM_CMD=(sudo pacman -S --noconfirm docker-compose) ;;
    docker-compose:zypper)     ITEM_CMD=(sudo zypper install -y docker-compose) ;;
    docker-service:*)          ITEM_CMD=(sudo systemctl enable --now docker) ;;
    docker-group:*)            ITEM_CMD=(sudo usermod -aG docker "$USER") ;;
    podman:brew)               ITEM_CMD=(brew install podman) ;;
    podman:apt)                ITEM_CMD=(sudo apt-get install -y podman) ;;
    podman:dnf)                ITEM_CMD=(sudo dnf install -y podman) ;;
    podman:pacman)             ITEM_CMD=(sudo pacman -S --noconfirm podman) ;;
    podman:zypper)             ITEM_CMD=(sudo zypper install -y podman) ;;
    podman-compose:brew)       ITEM_CMD=(brew install podman-compose) ;;
    podman-compose:apt)        ITEM_CMD=(sudo apt-get install -y podman-compose) ;;
    podman-compose:dnf)        ITEM_CMD=(sudo dnf install -y podman-compose) ;;
    podman-compose:pacman)     ITEM_CMD=(sudo pacman -S --noconfirm podman-compose) ;;
    podman-compose:zypper)     ITEM_CMD=(sudo zypper install -y podman-compose) ;;
    podman-machine-init:*)     ITEM_CMD=(podman machine init) ;;
    podman-machine-start:*)    ITEM_CMD=(podman machine start) ;;
    curl:brew)   ITEM_CMD=(brew install curl) ;;
    curl:apt)    ITEM_CMD=(sudo apt-get install -y curl) ;;
    curl:dnf)    ITEM_CMD=(sudo dnf install -y curl) ;;
    curl:pacman) ITEM_CMD=(sudo pacman -S --noconfirm curl) ;;
    curl:zypper) ITEM_CMD=(sudo zypper install -y curl) ;;
    git:brew)    ITEM_CMD=(brew install git) ;;
    git:apt)     ITEM_CMD=(sudo apt-get install -y git) ;;
    git:dnf)     ITEM_CMD=(sudo dnf install -y git) ;;
    git:pacman)  ITEM_CMD=(sudo pacman -S --noconfirm git) ;;
    git:zypper)  ITEM_CMD=(sudo zypper install -y git) ;;
    python:brew)   ITEM_CMD=(brew install python@3.12) ;;
    python:apt)    ITEM_CMD=(sudo apt-get install -y python3 python3-venv python3-pip); ITEM_NOTE="This installs the distribution's Python. If it is older than 3.12, use the deadsnakes PPA or pyenv instead (https://github.com/pyenv/pyenv)." ;;
    python:dnf)    ITEM_CMD=(sudo dnf install -y python3.12) ;;
    python:pacman) ITEM_CMD=(sudo pacman -S --noconfirm python python-pip) ;;
    python:zypper) ITEM_CMD=(sudo zypper install -y python312 python312-pip) ;;
    node:brew)   ITEM_CMD=(brew install node) ;;
    node:apt)    ITEM_CMD=(sudo apt-get install -y nodejs npm); ITEM_NOTE="The distribution's Node may be older than 18; https://github.com/nvm-sh/nvm gives a current one." ;;
    node:dnf)    ITEM_CMD=(sudo dnf install -y nodejs npm) ;;
    node:pacman) ITEM_CMD=(sudo pacman -S --noconfirm nodejs npm) ;;
    node:zypper) ITEM_CMD=(sudo zypper install -y nodejs npm) ;;
  esac
}

# The items for each way of getting a container engine on this system, in install order.
engine_items() { # engine_items docker|podman
  case "$1:$PKG" in
    docker:brew)   echo "docker-desktop" ;;
    docker:apt)    echo "docker-engine docker-compose docker-group" ;;
    docker:dnf|docker:pacman|docker:zypper) echo "docker-engine docker-compose docker-service docker-group" ;;
    podman:brew)   echo "podman podman-compose podman-machine-init podman-machine-start" ;;
    podman:*)      echo "podman podman-compose" ;;
  esac
}

# ---------------------------------------------------------------- asking and running
confirm() { # confirm "question": 0 = yes. The user must type y; Enter means no.
  (( YES )) && { echo "$1 [y/N] y   (--yes)"; return 0; }
  local a; printf '%s [y/N] ' "$1"; read -r a; [[ $a == y || $a == Y ]]
}

show_item() { # prints what an item is, why, and the exact command
  item_info "$1"
  echo
  echo "  * $ITEM_LABEL"
  echo "      why:     $ITEM_WHY"
  if (( ${#ITEM_CMD[@]} )); then echo "      command: ${ITEM_CMD[*]}"; else echo "      command: none for this system: install it by hand (see the hint printed above)"; fi
  [[ -n $ITEM_NOTE ]] && echo "      note:    $ITEM_NOTE"
  return 0
}

install_item() { # install_item key: shows it, asks y/N, runs it. Returns 0 if it ran successfully, 1 otherwise
  item_info "$1"
  show_item "$1"
  (( ${#ITEM_CMD[@]} )) || return 1
  if confirm "    Install this: $ITEM_LABEL?"; then
    if (( DRY )); then echo "      (dry run: not executed)"; return 0; fi
    "${ITEM_CMD[@]}" && return 0
    echo "      that command failed: see its output above" >&2
    return 1
  fi
  echo "      skipped"
  return 1
}

# ---------------------------------------------------------------- the checks
WSLTAG=""; (( IS_WSL )) && WSLTAG=", WSL"
echo "Hippocampus prerequisites ($OS${DISTRO:+, $DISTRO}$WSLTAG)"
(( IS_WSL )) && NOTES+=("WSL detected: Docker Desktop on Windows needs its WSL integration turned on for this distribution (Docker Desktop, Settings, Resources, WSL integration), or install an engine inside WSL.")

MISSING_ITEMS=()          # keys of single missing items (curl, git, python, node)
MISSING_REQUIRED=()       # the same, but only those without which Hippocampus cannot run or be developed
NEED_ENGINE=0

echo; echo "To run Hippocampus: one container engine (Docker OR Podman) with compose"

# Docker and Podman are checked separately and both are reported; ONE working engine is enough.
DOCKER_OK=0; PODMAN_OK=0
if have docker; then
  dv=$(docker --version 2>/dev/null | ver_of)
  if docker compose version >/dev/null 2>&1; then
    cv=$(docker compose version 2>/dev/null | ver_of)
    if docker info >/dev/null 2>&1; then info "Docker $dv with compose ${cv:-?}: installed and RUNNING"; DOCKER_OK=1
    else info "Docker $dv with compose ${cv:-?}: installed but the engine is NOT running or not reachable"; fi
  else info "Docker $dv: installed, but the 'docker compose' (v2) plugin is missing"; fi
else info "Docker: not installed"; fi
if have podman; then
  pv=$(podman --version 2>/dev/null | ver_of)
  if podman compose version >/dev/null 2>&1; then
    if podman info >/dev/null 2>&1; then info "Podman $pv with compose: installed and RUNNING"; PODMAN_OK=1
    else info "Podman $pv with compose: installed but not running (on macOS: podman machine start)"; fi
  else info "Podman $pv: installed, but 'podman compose' has no compose provider (install docker-compose or podman-compose)"; fi
else info "Podman: not installed"; fi

if (( DOCKER_OK )) || (( PODMAN_OK )); then
  if (( DOCKER_OK )); then ENGINE=docker; else ENGINE=podman; fi
  ok "a working container engine with compose: $ENGINE (Docker is used if both are present; set CONTAINER_ENGINE to choose)"
  mem=$("$ENGINE" info --format '{{.MemTotal}}' 2>/dev/null || true)
  if [[ $mem =~ ^[0-9]+$ ]]; then
    gb=$(( mem / 1024 / 1024 / 1024 ))
    if (( gb >= 2 )); then ok "the engine has about ${gb} GB of memory"
    else warn "the engine has only about ${gb} GB of memory" "Hippocampus wants at least 2 GB: raise the limit in Docker Desktop (Settings, Resources) or the VM."; fi
  fi
else
  NEED_ENGINE=1
  if have docker || have podman; then
    bad "no WORKING container engine: one is installed but not usable (see the lines above)" "Fix it (start Docker Desktop, 'sudo systemctl start docker', or 'podman machine start'; on Linux you may need the docker group), or install the missing compose part below."
  else
    bad "no container engine: you need Docker (with compose) OR Podman (with a compose provider)" "Either one works."
  fi
fi

echo; echo "Other requirements"
if (( BASH_VERSINFO[0] >= 3 )); then ok "bash ${BASH_VERSION%%(*}"; fi

if have curl; then ok "curl $(curl --version 2>/dev/null | ver_of)"
elif have python3; then ok "python3 (the launcher uses it when curl is missing)"
else bad "neither curl nor python3 is available" "The launcher needs one of them to wait for the service."; MISSING_ITEMS+=(curl); MISSING_REQUIRED+=(curl); fi

avail_kb=$(df -Pk . 2>/dev/null | awk 'NR==2{print $4}')
if [[ $avail_kb =~ ^[0-9]+$ ]]; then
  avail_gb=$(( avail_kb / 1024 / 1024 ))
  if (( avail_gb >= 3 )); then ok "free disk space here: about ${avail_gb} GB"
  else warn "free disk space here: about ${avail_gb} GB" "The two images need about 1.2 GB (2 GB with the browser engine) plus your data."; fi
fi

PORT_BUSY=0
if have lsof && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then PORT_BUSY=1
elif have ss && ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$PORT\$"; then PORT_BUSY=1; fi
if (( PORT_BUSY )); then warn "port $PORT is already in use on this machine" "Set HIPPO_PORT in hippocampus.env to a free port, or stop the program using $PORT."
else ok "port $PORT is free"; fi

if have curl; then
  code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://registry-1.docker.io/v2/ 2>/dev/null || true)
  if [[ $code == 401 || $code == 200 ]]; then ok "can reach the container registry (needed once, for the first build)"
  else warn "could not reach the container registry (registry-1.docker.io)" "The first build downloads images and the embedding model. Check your connection or proxy settings."; fi
fi

if (( DEV )); then
  echo; echo "To develop Hippocampus"
  if have git; then ok "git $(git --version | ver_of)"; else bad "git is missing" "https://git-scm.com/downloads"; MISSING_ITEMS+=(git); MISSING_REQUIRED+=(git); fi

  PYBIN=""
  for c in python3.13 python3.12 python3 python; do
    if have "$c"; then
      v=$("$c" --version 2>&1 | ver_of)
      if [[ -n $v ]] && ver_ge "$v" 3.12; then PYBIN=$c; break; fi
    fi
  done
  if [[ -n $PYBIN ]]; then
    ok "python $($PYBIN --version 2>&1 | ver_of) ($PYBIN)"
    if "$PYBIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then ok "python venv and pip are available"
    else bad "python is missing the venv or pip module" "Debian/Ubuntu: sudo apt-get install python3-venv python3-pip"; fi
  else
    found=$(python3 --version 2>&1 | ver_of || true)
    bad "Python 3.12 or newer is required${found:+ (found $found)}" "https://www.python.org/downloads/ or your package manager."
    MISSING_ITEMS+=(python); MISSING_REQUIRED+=(python)
  fi

  if have node && have npx; then
    nv=$(node --version | ver_of)
    if ver_ge "$nv" 18; then ok "node $nv and npx (for the Markdown linter and the dashboard JavaScript check)"
    else warn "node $nv is older than 18" "The Markdown linter needs node 18 or newer."; MISSING_ITEMS+=(node); fi
  else
    warn "node and npx are not installed" "Optional: only the Markdown linter and the dashboard JavaScript syntax check use them; scripts/lint.sh skips what it cannot run."
    MISSING_ITEMS+=(node)
  fi

  if [[ -x .venv/bin/python ]]; then ok "a virtualenv exists (.venv)"
  else warn "no .venv yet" "Create it with: scripts/prereqs.sh --dev --setup  (or: python3 -m venv .venv && .venv/bin/pip install -e \".[db,api,mcp,dev]\")"; fi

  # Optional test tools: without them two tests are skipped (the run report says so), and the dashboard tests cannot run.
  if [[ -x .venv/bin/python ]]; then
    if ! .venv/bin/python -c 'import playwright' >/dev/null 2>&1; then
      warn "playwright is not installed: the browser-crawl test and the dashboard (e2e) tests will be skipped" "Optional. scripts/prereqs.sh --dev --setup offers to install it (the 'browser' extra) and Chromium (about 150 MB)."
    elif ! .venv/bin/python -c 'import os; from playwright.sync_api import sync_playwright as s; p = s().start(); ok = os.path.exists(p.chromium.executable_path); p.stop(); raise SystemExit(0 if ok else 1)' >/dev/null 2>&1; then
      warn "Chromium is not installed for playwright: the browser-crawl test and the dashboard (e2e) tests will be skipped" "Optional. Install it with: .venv/bin/playwright install chromium (about 150 MB). On Linux, if it will not start: .venv/bin/playwright install-deps chromium (uses sudo)."
    else ok "playwright and Chromium (browser-crawl test and dashboard e2e tests can run)"; fi
    if ! .venv/bin/python -c 'import fastembed' >/dev/null 2>&1; then
      warn "fastembed is not installed: the embedding model test cannot run" "Optional. scripts/prereqs.sh --dev --setup offers to install it (the 'embed' extra)."
    else ok "fastembed (the embedding model test can run)"; fi
  fi
  NOTES+=("The embedding model test downloads the model (about 130 MB) and only runs with HIPPO_RUN_MODEL_TESTS=1, for example: HIPPO_RUN_MODEL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_embedder_model.py")
fi

# ---------------------------------------------------------------- what needs to be installed
echo; echo "================ what needs to be installed ================"
if (( NEED_ENGINE == 0 && ${#MISSING_ITEMS[@]} == 0 )); then
  echo "  Nothing. Everything required is already present."
else
  if (( NEED_ENGINE )); then
    echo
    echo "  REQUIRED: a container engine. You need ONE of these two (either works; you do not need both):"
    for e in docker podman; do
      echo
      if [[ $e == docker ]]; then echo "  Option A: Docker"; else echo "  Option B: Podman"; fi
      for k in $(engine_items $e); do show_item "$k"; done
    done
  fi
  for k in "${MISSING_ITEMS[@]}"; do
    req="optional"; for r in "${MISSING_REQUIRED[@]}"; do [[ $r == "$k" ]] && req="REQUIRED"; done
    echo; echo "  $req:"; show_item "$k"
  done
  echo
  if (( INSTALL )); then
    echo "  You will now be asked y/N before EACH item above. Anything you answer N to is not installed."
  else
    echo "  Nothing was installed. To be asked about each item, run this again with --install"
    echo "  (add --dry-run first to see exactly what would run; no acknowledgement is needed for a dry run)."
  fi
fi

# ---------------------------------------------------------------- the install pass (only with --install)
if (( INSTALL )) && (( NEED_ENGINE || ${#MISSING_ITEMS[@]} )); then
  if (( NEED_ENGINE )); then
    echo; echo "---- Container engine ----"
    engine_done=0
    for e in docker podman; do
      (( engine_done )) && break
      echo
      if confirm "Do you want to install $e as your container engine?"; then
        for k in $(engine_items "$e"); do install_item "$k" || true; done
        engine_done=1
      else
        echo "  not installing $e"
      fi
    done
    (( engine_done )) || echo "  No engine installed. Install Docker or Podman by hand, then run this script again."
  fi
  for k in "${MISSING_ITEMS[@]}"; do echo; install_item "$k" || true; done
fi

# ---------------------------------------------------------------- development environment
if (( DEV && SETUP )) && [[ -n ${PYBIN:-} ]]; then
  echo; echo "---- Development environment ----"
  echo "  * create .venv and install the project with its development tools"
  echo "      command: $PYBIN -m venv .venv && .venv/bin/pip install -e \".[db,api,mcp,dev]\"   (about 200 MB, needs internet)"
  if confirm "    Do this?"; then
    if (( DRY )); then echo "      (dry run: not executed)"
    else
      "$PYBIN" -m venv .venv && .venv/bin/pip install -q -e ".[db,api,mcp,dev]" \
        && echo "      done. Next: scripts/check.sh --no-db   (or scripts/check.sh with a container engine for the database tests)"
    fi
  else echo "      skipped"; fi

  # Optional test tools. Each is its own question; nothing here is needed to run Hippocampus.
  if [[ -x .venv/bin/python ]]; then
    echo "  * (optional) browser tests: the playwright package and the Chromium browser"
    echo "      command: .venv/bin/pip install -e \".[browser]\" && .venv/bin/playwright install chromium   (about 150 MB, needs internet)"
    if confirm "    Do this?"; then
      if (( DRY )); then echo "      (dry run: not executed)"
      else .venv/bin/pip install -q -e ".[browser]" && .venv/bin/playwright install chromium; fi
    else echo "      skipped"; fi
    echo "  * (optional) embedding model test: the fastembed package (the model itself downloads when the test runs)"
    echo "      command: .venv/bin/pip install -e \".[embed]\"   (needs internet)"
    if confirm "    Do this?"; then
      if (( DRY )); then echo "      (dry run: not executed)"
      else .venv/bin/pip install -q -e ".[embed]"; fi
    else echo "      skipped"; fi
  fi
fi

# ---------------------------------------------------------------- summary
echo; echo "================ summary ================"
echo "  ok: ${#OK[@]}   warnings: ${#WARN[@]}   missing: ${#BAD[@]}"
for n in "${NOTES[@]}"; do echo "  note: $n"; done
if (( ${#BAD[@]} )); then
  echo "  Missing:"; for b in "${BAD[@]}"; do echo "    - $b"; done
  (( INSTALL && ! DRY )) && echo "  If you installed something, run this script again to confirm it works (a new engine may need you to log in again first)."
  echo "RESULT: not ready"; exit 1
fi
echo "RESULT: ready. Start Hippocampus with:  ./hippocampus up"
