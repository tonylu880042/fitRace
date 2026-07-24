#!/bin/bash
set -euo pipefail

# One-click deployment for fitRace Hub and Edge.
# See DEPLOYMENT.md for usage.

HUB_TARGET="${HUB_TARGET:-tony@192.168.0.130}"
EDGE_TARGET="${EDGE_TARGET:-tony@192.168.0.130}"
EDGE_DEPLOY_PATH="${EDGE_DEPLOY_PATH:-/home/tony/fitRace}"
DRY_RUN=0 SKIP_TESTS=0 ALLOW_DIRTY=0
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

info() { echo "[INFO] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }
warn() { echo "[WARN] $*" >&2; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then echo "DRY: $*"; else "$@"; fi
}

ssh_run() {
  local target="$1"; shift
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "DRY: ssh -o ConnectTimeout=6 '$target' $*"
  else
    ssh -o ConnectTimeout=6 "$target" "$@"
  fi
}

check_repo_clean() {
  cd "$REPO_ROOT"
  local dirty=$(git status --porcelain)
  if [[ -n "$dirty" ]] && [[ $ALLOW_DIRTY -eq 0 ]]; then
    error "Uncommitted changes. Pass --allow-dirty or commit changes."
  fi
  [[ -z "$dirty" ]] || warn "Uncommitted changes present."
}

run_tests() {
  [[ $SKIP_TESTS -eq 1 ]] && { info "Skipping tests"; return 0; }
  info "Running tests..."
  cd "$REPO_ROOT"
  python3 -m pytest tests/ -q || error "Tests failed."
}

preflight_check() {
  [[ $DRY_RUN -eq 1 ]] && { info "Pre-flight skipped (--dry-run)"; return 0; }
  ssh -o ConnectTimeout=6 -o BatchMode=yes "$1" true 2>/dev/null || \
    error "Cannot connect to $1. On venue LAN?"
}

# Poll a health URL over ssh until it returns "ok" or we give up. A Pi cold
# start (systemd restart + Python import + MQTT connect) routinely needs
# more than a couple of seconds, so a single shot false-fails a good deploy.
wait_for_health() {
  local target="$1" url="$2" tries=15
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "DRY: ssh -o ConnectTimeout=6 '$target' curl -s -m 8 $url | grep -q ok  (retried until healthy)"
    return 0
  fi
  local i
  for ((i = 1; i <= tries; i++)); do
    if ssh -o ConnectTimeout=6 "$target" "curl -s -m 8 $url | grep -q ok"; then
      info "Health check passed (attempt $i)."
      return 0
    fi
    sleep 2
  done
  return 1
}

deploy_hub() {
  local target="${1:-$HUB_TARGET}"
  info "Deploying hub to $target..."
  preflight_check "$target"

  local release_name="hub-manual-$(date +%Y%m%d%H%M%S)"
  local release_path="/opt/fitracestudio/releases/$release_name"
  info "Release: $release_name"

  [[ $DRY_RUN -eq 0 ]] && prev_release=$(ssh -o ConnectTimeout=6 "$target" \
    "readlink /opt/fitracestudio/current" 2>/dev/null || echo "")

  local excludes=()
  local e
  for e in __pycache__ .git data tmp scratch output .venv node_modules edge.log wifi_state.json .claude; do
    excludes+=(--exclude="$e")
  done
  run rsync -ac "${excludes[@]}" --rsync-path="sudo rsync" \
    -e "ssh -o ConnectTimeout=6" "$REPO_ROOT/" "$target:$release_path/"

  ssh_run "$target" "sudo ln -sfn $release_path /opt/fitracestudio/current"
  ssh_run "$target" "sudo systemctl restart fitracestudio-hub.service"
  if ! wait_for_health "$target" "http://localhost:8000/health"; then
    [[ -n "${prev_release:-}" ]] && \
      warn "Roll back with: deploy.sh rollback-hub $(basename "$prev_release") $target"
    error "Hub health check FAILED after deploy — new release may be broken."
  fi

  echo ""
  echo "Hub deploy completed: $release_name at $release_path"
  [[ -n "${prev_release:-}" ]] && echo "Rollback: deploy.sh rollback-hub $(basename "$prev_release") $target"
  echo ""
}

deploy_edge() {
  local target="${1:-$EDGE_TARGET}" path="${2:-$EDGE_DEPLOY_PATH}"
  info "Deploying edge to $target at $path..."
  preflight_check "$target"

  # config.json is device-local (equipment bindings, mqtt_host) — never let a
  # deploy overwrite or --delete it, or the venue's setup is lost.
  run rsync -ac --delete --exclude=__pycache__ --exclude=config.json \
    -e "ssh -o ConnectTimeout=6" \
    "$REPO_ROOT/edge_node/" "$target:$path/edge_node/"
  run rsync -ac --exclude=__pycache__ -e "ssh -o ConnectTimeout=6" \
    "$REPO_ROOT/fitrace_common/" "$target:$path/fitrace_common/"

  ssh_run "$target" "sudo systemctl restart fitracestudio-edge.service"
  ssh_run "$target" "sudo systemctl restart fitracestudio-edge-web-config.service"
  wait_for_health "$target" "http://localhost:8001/health" || \
    error "Edge health check FAILED after deploy — check journalctl on $target."

  echo ""
  echo "Edge deploy completed at $target:$path"
  echo ""
}

rollback_hub() {
  local release_name="$1" target="${2:-$HUB_TARGET}"
  local release_path="/opt/fitracestudio/releases/$release_name"
  info "Rolling back hub to $release_name..."
  preflight_check "$target"

  ssh_run "$target" "test -d $release_path" || error "Release not found: $release_name"
  ssh_run "$target" "sudo ln -sfn $release_path /opt/fitracestudio/current"
  ssh_run "$target" "sudo systemctl restart fitracestudio-hub.service"
  wait_for_health "$target" "http://localhost:8000/health" || \
    warn "Health check incomplete — verify $target manually."

  echo ""
  echo "Rollback completed: $release_name"
  echo ""
}

list_releases() {
  local target="${1:-$HUB_TARGET}"
  info "Hub releases on $target:"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "DRY: ssh -o ConnectTimeout=6 '$target' ls -1 /opt/fitracestudio/releases"
  else
    preflight_check "$target"
    local current=$(ssh -o ConnectTimeout=6 "$target" "readlink /opt/fitracestudio/current" 2>/dev/null || echo "")
    ssh -o ConnectTimeout=6 "$target" "ls -1 /opt/fitracestudio/releases 2>/dev/null" | while read -r r; do
      [[ "$r" == "$(basename "$current")" ]] && echo "  * $r (current)" || echo "    $r"
    done
  fi
  echo ""
}

usage() {
  cat <<EOF
deploy.sh [FLAGS] <subcommand> [args]

Subcommands:
  hub [user@host]              Deploy hub (default: tony@192.168.0.130)
  edge [user@host] [path]      Deploy edge (default: tony@192.168.0.130, /home/tony/fitRace)
  all [user@host]              Deploy hub and edge
  rollback-hub <name> [host]   Rollback hub to release
  list [user@host]             List hub releases

Flags (position-independent):
  --dry-run       Print commands without executing
  --skip-tests    Skip pytest
  --allow-dirty   Allow uncommitted changes
EOF
  exit 0
}

main() {
  [[ $# -eq 0 ]] && usage

  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --skip-tests) SKIP_TESTS=1; shift ;;
      --allow-dirty) ALLOW_DIRTY=1; shift ;;
      *) args+=("$1"); shift ;;
    esac
  done
  set -- "${args[@]}"

  [[ $# -eq 0 ]] && usage

  local cmd="$1"; shift
  # ponytail: rollback and list never ship files, so a dirty tree must not block them
  case "$cmd" in hub|edge|all) check_repo_clean ;; esac

  case "$cmd" in
    hub) run_tests; deploy_hub "$@" ;;
    edge) run_tests; deploy_edge "$@" ;;
    all) run_tests; deploy_hub "$@"; deploy_edge "$@" ;;
    rollback-hub) rollback_hub "$@" ;;
    list) list_releases "$@" ;;
    -h|--help) usage ;;
    *) error "Unknown subcommand: $cmd" ;;
  esac
}

main "$@"
