#!/bin/bash
#
# cyclo_intelligence container helper. It auto-detects ARCH from uname -m
# and manages the main runtime plus optional policy containers.
#
# Usage:
#   docker/container.sh start              # → cyclo_intelligence
#   docker/container.sh start-lerobot      # → lerobot (idle until LOAD)
#   docker/container.sh start-groot        # → groot (idle until LOAD)
#   docker/container.sh enter              # → shell in cyclo_intelligence
#   docker/container.sh enter-lerobot      # → shell in lerobot_server
#   docker/container.sh enter-groot        # → shell in groot_server
#   docker/container.sh logs               # → compose logs -f
#   docker/container.sh status             # → s6 svstat on all containers
#   docker/container.sh stop               # → compose down
#   docker/container.sh help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f ${SCRIPT_DIR}/docker-compose.yml"
# Standard docker-compose override convention. Auto-discovery is
# disabled when -f is passed explicitly (above), so we re-enable it
# manually: if a sibling docker-compose.override.yml exists, layer it
# on top. Lets developers shadow image tags / mounts locally without
# editing the canonical compose file.
[ -f "${SCRIPT_DIR}/docker-compose.override.yml" ] \
    && COMPOSE="${COMPOSE} -f ${SCRIPT_DIR}/docker-compose.override.yml"

MAIN_SERVICE="cyclo_intelligence"
MAIN_CONTAINER="cyclo_intelligence"
LEROBOT_SERVICE="lerobot"
LEROBOT_CONTAINER="${LEROBOT_CONTAINER_NAME:-lerobot_server}"
GROOT_SERVICE="groot"
GROOT_CONTAINER="${GROOT_CONTAINER_NAME:-groot_server}"

# Auto-detect host architecture for Dockerfile / image tag selection
MACHINE_ARCH=$(uname -m)
if [ "$MACHINE_ARCH" = "aarch64" ] || [ "$MACHINE_ARCH" = "arm64" ]; then
    export ARCH="arm64"
    echo "[container.sh] Detected ARM64 architecture (Jetson)"
else
    export ARCH="amd64"
    echo "[container.sh] Detected AMD64 architecture (x86_64)"
fi

# Optional opt-in rebuild. Default is to use the pre-built Hub image.
# Pass `--build` (or `-b`) on any start* command to rebuild from source.
BUILD_FLAG=""
NEW_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --build|-b) BUILD_FLAG="--build" ;;
        *)          NEW_ARGS+=("$arg") ;;
    esac
done
set -- "${NEW_ARGS[@]}"

# Pre-create host bind-mount targets so docker doesn't auto-create them
# as root-owned directories (which then can't be written to from the
# host without sudo). In auto mode, prefer SSD-backed storage when it is
# usable and fall back to repo-local storage on development machines.
ensure_host_dir() {
    [ -d "$1" ] || mkdir -p "$1"
}

storage_root_usable() {
    local root="$1"
    local probe="${root}/.cyclo-write-test.$$"

    mkdir -p "$root" 2>/dev/null || return 1
    mkdir "$probe" 2>/dev/null || return 1
    rmdir "$probe" 2>/dev/null || true
}

setup_storage() {
    local storage_mode="${CYCLO_STORAGE_MODE:-auto}"
    local ssd_root="${CYCLO_SSD_ROOT:-/mnt/ssd/cyclo_intelligence}"
    local local_workspace="${CYCLO_LOCAL_WORKSPACE_DIR:-${SCRIPT_DIR}/workspace}"
    local local_huggingface="${CYCLO_LOCAL_HUGGINGFACE_DIR:-${SCRIPT_DIR}/huggingface}"
    local default_workspace
    local default_huggingface
    local storage_label

    case "$storage_mode" in
        auto)
            if storage_root_usable "$ssd_root"; then
                default_workspace="${ssd_root}/workspace"
                default_huggingface="${ssd_root}/huggingface"
                storage_label="SSD"
            else
                default_workspace="$local_workspace"
                default_huggingface="$local_huggingface"
                storage_label="local"
            fi
            ;;
        ssd)
            if ! storage_root_usable "$ssd_root"; then
                echo "[container.sh] Error: SSD storage root is not writable: $ssd_root" >&2
                echo "[container.sh] Mount/create the SSD path, choose CYCLO_STORAGE_MODE=local, or set CYCLO_WORKSPACE_DIR/CYCLO_HUGGINGFACE_DIR." >&2
                exit 1
            fi
            default_workspace="${ssd_root}/workspace"
            default_huggingface="${ssd_root}/huggingface"
            storage_label="SSD"
            ;;
        local)
            default_workspace="$local_workspace"
            default_huggingface="$local_huggingface"
            storage_label="local"
            ;;
        *)
            echo "[container.sh] Error: unknown CYCLO_STORAGE_MODE='$storage_mode' (expected auto, ssd, or local)" >&2
            exit 1
            ;;
    esac

    CYCLO_WORKSPACE_DIR="${CYCLO_WORKSPACE_DIR:-$default_workspace}"
    CYCLO_HUGGINGFACE_DIR="${CYCLO_HUGGINGFACE_DIR:-$default_huggingface}"
    export CYCLO_WORKSPACE_DIR
    export CYCLO_HUGGINGFACE_DIR

    ensure_host_dir "${CYCLO_WORKSPACE_DIR}"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/dataset"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/rosbag2"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/lerobot"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/model"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/model/lerobot"
    ensure_host_dir "${CYCLO_WORKSPACE_DIR}/model/groot"
    ensure_host_dir "${CYCLO_HUGGINGFACE_DIR}"

    echo "[container.sh] Using ${storage_label} storage"
    echo "[container.sh]   workspace:   ${CYCLO_WORKSPACE_DIR}"
    echo "[container.sh]   huggingface: ${CYCLO_HUGGINGFACE_DIR}"
}

CYCLO_AGENT_SOCKETS_DIR="${CYCLO_AGENT_SOCKETS_DIR:-/var/run/robotis/agent_sockets/cyclo_intelligence}"
export CYCLO_AGENT_SOCKETS_DIR
mkdir -p "$CYCLO_AGENT_SOCKETS_DIR" 2>/dev/null \
    || sudo mkdir -p "$CYCLO_AGENT_SOCKETS_DIR" 2>/dev/null \
    || true

# X11 forwarding for UI windows (rviz, plotjuggler, etc.) when started
# from an interactive shell. Silently skipped if DISPLAY isn't set.
setup_x11() {
    if [ -n "$DISPLAY" ]; then
        xhost +local:docker > /dev/null 2>&1 || true
    fi
}

container_running() {
    docker ps --format '{{.Names}}' | grep -q "^$1\$"
}

show_help() {
    cat <<EOF
Usage: $0 <command>

Main image (cyclo_intelligence):
  start            Build (if needed) and start cyclo_intelligence
  enter            Open an interactive bash in cyclo_intelligence
  logs             Tail cyclo_intelligence logs

LeRobot policy container:
  start-lerobot    Build + start lerobot. Container boots idle and
                   only configures itself once orchestrator dispatches
                   InferenceCommand.LOAD with a robot_type.
  enter-lerobot    Open an interactive bash in lerobot_server

GR00T policy container:
  start-groot      Build + start groot (N1.7 baseline). Same boot-idle
                   + LOAD-time configure pattern as lerobot.
  enter-groot      Open an interactive bash in groot_server

Lifecycle:
  status           s6-svstat on all containers (when running)
  stop             compose down (prompts for confirmation)
  help             Show this help

Flags (any start* command):
  --build, -b      Rebuild image from local Dockerfile instead of using
                   the pre-built image pulled from Docker Hub. Default
                   is to use the pulled image (fast, no source build
                   required). Use this only when iterating on Dockerfile.

Environment:
  GPU_ARCH         default | blackwell   (optional, amd64 only)
  VERSION          image tag version (default: 0.1.12 for cyclo)
  ROS_DOMAIN_ID    default 30
  CYCLO_STORAGE_MODE
                   auto | ssd | local (default auto). Auto uses
                   /mnt/ssd/cyclo_intelligence when writable, otherwise
                   docker/workspace and docker/huggingface.
  CYCLO_WORKSPACE_DIR / CYCLO_HUGGINGFACE_DIR
                   Override the host bind-mount paths directly.
EOF
}

start_main() {
    setup_storage
    setup_x11
    echo "[container.sh] Pulling pre-built images (ignoring local-only failures)..."
    $COMPOSE pull --ignore-pull-failures "$MAIN_SERVICE" || true
    echo "[container.sh] Starting $MAIN_SERVICE (ARCH=$ARCH${BUILD_FLAG:+, rebuild on})..."
    $COMPOSE up -d $BUILD_FLAG "$MAIN_SERVICE"
    echo "[container.sh] Done. 'docker/container.sh status' to check s6 services."
}

start_lerobot() {
    setup_storage
    setup_x11
    echo "[container.sh] Pulling pre-built images..."
    $COMPOSE pull --ignore-pull-failures "$LEROBOT_SERVICE" || true
    echo "[container.sh] Starting $LEROBOT_SERVICE (ARCH=$ARCH${BUILD_FLAG:+, rebuild on})..."
    $COMPOSE up -d $BUILD_FLAG "$LEROBOT_SERVICE"
}

start_groot() {
    setup_storage
    setup_x11
    echo "[container.sh] Pulling pre-built images..."
    $COMPOSE pull --ignore-pull-failures "$GROOT_SERVICE" || true
    echo "[container.sh] Starting $GROOT_SERVICE (ARCH=$ARCH${BUILD_FLAG:+, rebuild on})..."
    $COMPOSE up -d $BUILD_FLAG "$GROOT_SERVICE"
}

enter_main() {
    if ! container_running "$MAIN_CONTAINER"; then
        echo "Error: $MAIN_CONTAINER is not running. Run 'start' first." >&2
        exit 1
    fi
    setup_x11
    docker exec -it "$MAIN_CONTAINER" bash
}

enter_lerobot() {
    if ! container_running "$LEROBOT_CONTAINER"; then
        echo "Error: $LEROBOT_CONTAINER is not running. Run 'start-lerobot' first." >&2
        exit 1
    fi
    docker exec -it "$LEROBOT_CONTAINER" bash
}

enter_groot() {
    if ! container_running "$GROOT_CONTAINER"; then
        echo "Error: $GROOT_CONTAINER is not running. Run 'start-groot' first." >&2
        exit 1
    fi
    docker exec -it "$GROOT_CONTAINER" bash
}

show_logs() {
    $COMPOSE logs -f
}

show_status() {
    echo "=== Containers ==="
    docker ps --format '{{.Names}}\t{{.Status}}' \
        | grep -E "^(${MAIN_CONTAINER}|${LEROBOT_CONTAINER}|${GROOT_CONTAINER})\\b" \
        || echo "(none running)"

    # s6-overlay installs s6-svstat under /package/admin/s6-*/command/
    # rather than a stable PATH location, so resolve it dynamically.
    # `sh -c` inside docker exec lets the inner shell glob the version
    # directory without depending on bash being available.
    local svstat_setup='
        S6_SVSTAT=$(ls /package/admin/s6-*/command/s6-svstat 2>/dev/null | head -1)
        [ -z "$S6_SVSTAT" ] && S6_SVSTAT=$(command -v s6-svstat 2>/dev/null)
        [ -z "$S6_SVSTAT" ] && { echo "  (s6-svstat not found)"; exit 0; }
    '

    if container_running "$MAIN_CONTAINER"; then
        echo ""
        echo "=== ${MAIN_CONTAINER} s6 services ==="
        docker exec "$MAIN_CONTAINER" sh -c "
            ${svstat_setup}
            for svc in /run/service/*/; do
                name=\$(basename \"\$svc\")
                printf '  %-30s %s\n' \"\$name\" \"\$(\$S6_SVSTAT \"\$svc\" 2>&1)\"
            done
        " || true
    fi

    for cont in "$LEROBOT_CONTAINER" "$GROOT_CONTAINER"; do
        if container_running "$cont"; then
            echo ""
            # Not every policy container uses s6-overlay (e.g. lerobot
            # has PID 1 running executor.py directly). Detect /run/service
            # first; fall back to top-level process list otherwise.
            if docker exec "$cont" sh -c '[ -d /run/service ]' 2>/dev/null; then
                echo "=== ${cont} s6 services ==="
                docker exec "$cont" sh -c "
                    ${svstat_setup}
                    for svc in /run/service/*/; do
                        name=\$(basename \"\$svc\")
                        printf '  %-30s %s\n' \"\$name\" \"\$(\$S6_SVSTAT \"\$svc\" 2>&1)\"
                    done
                " || true
            else
                echo "=== ${cont} processes (no s6-overlay) ==="
                docker exec "$cont" sh -c 'ps -eo pid,user,comm,args | head -8' || true
            fi
        fi
    done
}

stop_all() {
    echo "Warning: this will stop and remove all compose-managed containers."
    read -p "Are you sure? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        $COMPOSE down
    else
        echo "Cancelled."
    fi
}

case "${1:-help}" in
    start)           start_main ;;
    start-lerobot)   start_lerobot ;;
    start-groot)     start_groot ;;
    enter)           enter_main ;;
    enter-lerobot)   enter_lerobot ;;
    enter-groot)     enter_groot ;;
    logs)            show_logs ;;
    status)          show_status ;;
    stop)            stop_all ;;
    help|-h|--help)  show_help ;;
    *)
        echo "Error: unknown command '$1'" >&2
        show_help
        exit 1
        ;;
esac
