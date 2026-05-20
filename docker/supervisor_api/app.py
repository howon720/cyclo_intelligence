#!/usr/bin/env python3
#
# Copyright 2025 ROBOTIS CO., LTD.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""supervisor_api — PLAN §4.7 + §4.8 control plane.

Thin FastAPI layer sitting between the UI (via nginx /api/) and:
  (a) the s6-rc service manager inside this container, for the ROS2
      longruns (orchestrator / cyclo_data / web_video_server), and
  (b) the host Docker daemon, for policy containers that ship
      out-of-image (lerobot — and groot once D10-groot lands).

Run as:
    uvicorn supervisor_api.app:app \
        --host "${CYCLO_SUPERVISOR_API_HOST:-127.0.0.1}" \
        --port "${CYCLO_SUPERVISOR_API_PORT:-8100}"

nginx proxies /api/ → 127.0.0.1:8100 (Step 6-E).

Environment overrides:
    CYCLO_SUPERVISOR_API_HOST         bind host (default 127.0.0.1)
    CYCLO_SUPERVISOR_API_PORT         bind port (default 8100)
    CYCLO_SUPERVISOR_API_REPO_MOUNT   in-container path of the repo bind-mount
                                      (default /root/ros2_ws/src/cyclo_intelligence)
    CYCLO_SUPERVISOR_API_COMPOSE_FILE absolute path to docker-compose.yml inside
                                      this container (default <repo-mount>/docker/docker-compose.yml)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


logger = logging.getLogger("supervisor_api")


# -- s6-rc runner --------------------------------------------------------------


# Names the UI may start/stop. Kept explicit so a stray POST can't
# poke at s6-agent or the log pipelines.
_USER_SERVICES: tuple[str, ...] = (
    "orchestrator",
    "cyclo_data",
    "web_video_server",
)


@dataclass
class _S6Result:
    rc: int
    stdout: str
    stderr: str


async def _run(
    *cmd: str,
    timeout: float = 10.0,
    env: Optional[Dict[str, str]] = None,
) -> _S6Result:
    """Run a subprocess, return stdout/stderr/rc."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, f"{cmd[0]} timed out after {timeout}s")
    return _S6Result(
        rc=proc.returncode or 0,
        stdout=stdout.decode(errors="replace").strip(),
        stderr=stderr.decode(errors="replace").strip(),
    )


def _require_known_service(name: str) -> None:
    if name not in _USER_SERVICES:
        raise HTTPException(
            404,
            f"Unknown service '{name}'. Known: {', '.join(_USER_SERVICES)}",
        )


# -- API models ----------------------------------------------------------------


class ServiceStatus(BaseModel):
    name: str
    state: Literal["up", "down", "unknown"]
    pid: Optional[int] = None
    uptime_s: Optional[int] = None
    raw: str


class ServiceList(BaseModel):
    services: List[ServiceStatus]


class ActionResult(BaseModel):
    ok: bool
    message: str


class HealthResponse(BaseModel):
    ok: bool
    container: str
    s6_ready: bool


class BackendStatus(BaseModel):
    name: str
    image: str
    image_pulled: bool
    container_state: Literal["running", "exited", "not_created", "unknown"]
    container_id: Optional[str] = None
    raw_state: Optional[str] = None
    services: List[ServiceStatus] = Field(default_factory=list)


# -- Backend (policy container) wiring -----------------------------------------


# Compose file + repo-mount paths inside this container — the cyclo_intelligence
# service bind-mounts the repo root at /root/ros2_ws/src/cyclo_intelligence by
# default (live edits during dev). Override both with env vars when the mount
# point differs (e.g. running supervisor_api on the host for debugging).
_CYCLO_REPO_MOUNT = os.environ.get(
    "CYCLO_SUPERVISOR_API_REPO_MOUNT",
    "/root/ros2_ws/src/cyclo_intelligence",
)
_COMPOSE_FILE_IN_CONTAINER = os.environ.get(
    "CYCLO_SUPERVISOR_API_COMPOSE_FILE",
    f"{_CYCLO_REPO_MOUNT}/docker/docker-compose.yml",
)
_COMPOSE_OVERRIDE_IN_CONTAINER = os.path.join(
    os.path.dirname(_COMPOSE_FILE_IN_CONTAINER),
    "docker-compose.override.yml",
)


def _detect_arch() -> str:
    machine = os.uname().machine
    return "arm64" if machine in ("aarch64", "arm64") else "amd64"


_BACKEND_ARCH = os.environ.get("ARCH", _detect_arch())


# Image versions are hardcoded per backend below since each service has
# its own release cadence. ARCH still falls back to a uname-based sniff
# because compose only interpolates env vars on the host invocation, so
# inside the container the env var isn't set.
_BACKENDS: Dict[str, Dict[str, str]] = {
    "lerobot": {
        "service": "lerobot",
        "container": "lerobot_server",
        "image": f"robotis/lerobot-zenoh:1.0.1-{_BACKEND_ARCH}",
    },
    "groot": {
        "service": "groot",
        "container": "groot_server",
        "image": f"robotis/groot-zenoh:1.2.1-{_BACKEND_ARCH}",
    },
}


def _docker_client() -> docker.DockerClient:
    return docker.from_env()


def _require_known_backend(name: str) -> Dict[str, str]:
    if name not in _BACKENDS:
        known = ", ".join(_BACKENDS) or "(none)"
        raise HTTPException(
            404, f"Unknown backend '{name}'. Known: {known}"
        )
    return _BACKENDS[name]


_HOST_PROJECT_DIR_CACHE: Optional[str] = None


def _host_project_dir() -> Optional[str]:
    """Resolve the host-side path to cyclo_intelligence/docker/ by
    inspecting our own container's mounts.

    compose CLI invoked from inside a container still talks to the host
    docker daemon, so any relative path in docker-compose.yml
    (./workspace, ../cyclo_brain/sdk/...) must resolve to the host
    filesystem — not the bind-mount path inside us. We pass this dir
    via --project-directory so compose's relative-path resolution
    points at the host tree even though we're calling from inside.
    """
    global _HOST_PROJECT_DIR_CACHE
    if _HOST_PROJECT_DIR_CACHE is not None:
        return _HOST_PROJECT_DIR_CACHE
    own_id = os.environ.get("HOSTNAME")
    if not own_id:
        return None
    try:
        ctr = _docker_client().containers.get(own_id)
    except DockerException as e:
        logger.warning("self-inspect failed: %s", e)
        return None
    for mount in ctr.attrs.get("Mounts", []):
        if mount.get("Destination") == _CYCLO_REPO_MOUNT:
            host_repo = mount.get("Source")
            if host_repo:
                _HOST_PROJECT_DIR_CACHE = os.path.join(host_repo, "docker")
                return _HOST_PROJECT_DIR_CACHE
    logger.warning(
        "no mount found for %s — compose CLI relative paths will resolve "
        "against the in-container path, which the host docker daemon "
        "cannot satisfy",
        _CYCLO_REPO_MOUNT,
    )
    return None


def _compose_base_cmd() -> List[str]:
    cmd = ["docker", "compose"]
    project_dir = _host_project_dir()
    if project_dir:
        cmd += ["--project-directory", project_dir]
    cmd += ["-f", _COMPOSE_FILE_IN_CONTAINER]
    if os.path.exists(_COMPOSE_OVERRIDE_IN_CONTAINER):
        cmd += ["-f", _COMPOSE_OVERRIDE_IN_CONTAINER]
    return cmd


def _backend_image_candidates(spec: Dict[str, str]) -> List[str]:
    candidates = [spec["image"]]
    alt = spec.get("image_alt")
    if alt and alt not in candidates:
        candidates.append(alt)
    return candidates


def _local_backend_image(client: docker.DockerClient, spec: Dict[str, str]) -> Optional[str]:
    for image in _backend_image_candidates(spec):
        try:
            client.images.get(image)
            return image
        except ImageNotFound:
            continue
    return None


def _container_raw_state(container) -> str:
    try:
        container.reload()
    except DockerException:
        pass
    return container.attrs.get("State", {}).get("Status", "unknown")


def _backend_service_statuses(container, raw_state: str) -> List[ServiceStatus]:
    """Inspect the two s6-managed policy runtime processes."""
    service_names = ("main-runtime", "engine-process")
    if raw_state != "running":
        return []

    script = r"""
S6_SVSTAT=$(ls /package/admin/s6-*/command/s6-svstat 2>/dev/null | head -1)
[ -z "$S6_SVSTAT" ] && S6_SVSTAT=$(command -v s6-svstat 2>/dev/null)
if [ -z "$S6_SVSTAT" ]; then
  for svc in main-runtime engine-process; do
    printf '%s\ts6-svstat not found\n' "$svc"
  done
  exit 0
fi
for svc in main-runtime engine-process; do
  svdir="/run/service/$svc"
  if [ -d "$svdir" ]; then
    raw=$("$S6_SVSTAT" "$svdir" 2>&1)
    printf '%s\t%s\n' "$svc" "$raw"
  else
    printf '%s\tnot registered\n' "$svc"
  fi
done
"""
    try:
        result = container.exec_run(["sh", "-lc", script])
    except DockerException as e:
        return [
            ServiceStatus(
                name=name,
                state="unknown",
                raw=f"inspect failed: {e}",
            )
            for name in service_names
        ]

    output = result.output.decode(errors="replace") if result.output else ""
    statuses: List[ServiceStatus] = []
    seen = set()
    for line in output.splitlines():
        if "\t" not in line:
            continue
        name, raw = line.split("\t", 1)
        if name not in service_names:
            continue
        parsed = _parse_svstat(raw)
        statuses.append(ServiceStatus(name=name, raw=raw, **parsed))
        seen.add(name)

    for name in service_names:
        if name not in seen:
            statuses.append(
                ServiceStatus(name=name, state="unknown", raw="not reported")
            )
    return statuses


# -- parsing -------------------------------------------------------------------


def _parse_svstat(raw: str) -> dict:
    """Best-effort parse of s6-svstat output.

    Example: 'up (pid 1234) 37 seconds' or 'down 3 seconds, normally up'.
    We only need state + pid + uptime; everything else we return verbatim.
    """
    tokens = raw.split()
    state: Literal["up", "down", "unknown"] = "unknown"
    if tokens:
        if tokens[0] == "up":
            state = "up"
        elif tokens[0] == "down":
            state = "down"

    pid: Optional[int] = None
    uptime_s: Optional[int] = None
    if "(pid" in raw:
        try:
            pid_part = raw.split("(pid", 1)[1].split(")", 1)[0].strip().split()[0]
            pid = int(pid_part)
        except (ValueError, IndexError):
            pass
    if "seconds" in raw:
        # token before "seconds" is the uptime
        try:
            idx = tokens.index("seconds")
            uptime_s = int(tokens[idx - 1])
        except (ValueError, IndexError):
            pass

    return {"state": state, "pid": pid, "uptime_s": uptime_s}


# -- FastAPI app ---------------------------------------------------------------


app = FastAPI(
    title="cyclo_intelligence supervisor_api",
    description=__doc__,
    version="0.2.0",
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    container = os.environ.get("HOSTNAME", "unknown")
    s6_ready = os.path.isdir("/run/service")
    return HealthResponse(ok=True, container=container, s6_ready=s6_ready)


@app.get("/services", response_model=ServiceList)
async def list_services() -> ServiceList:
    items: List[ServiceStatus] = []
    for name in _USER_SERVICES:
        svdir = f"/run/service/{name}"
        if not os.path.isdir(svdir):
            items.append(
                ServiceStatus(name=name, state="unknown", raw="not registered")
            )
            continue
        result = await _run("s6-svstat", svdir)
        parsed = _parse_svstat(result.stdout)
        items.append(
            ServiceStatus(
                name=name,
                state=parsed["state"],
                pid=parsed["pid"],
                uptime_s=parsed["uptime_s"],
                raw=result.stdout,
            )
        )
    return ServiceList(services=items)


@app.get("/services/{name}/status", response_model=ServiceStatus)
async def service_status(name: str) -> ServiceStatus:
    _require_known_service(name)
    svdir = f"/run/service/{name}"
    if not os.path.isdir(svdir):
        return ServiceStatus(name=name, state="unknown", raw="not registered")
    result = await _run("s6-svstat", svdir)
    parsed = _parse_svstat(result.stdout)
    return ServiceStatus(name=name, raw=result.stdout, **parsed)


@app.post("/services/{name}/start", response_model=ActionResult)
async def service_start(name: str) -> ActionResult:
    _require_known_service(name)
    # s6-rc -u change <name> brings the service up (idempotent).
    result = await _run("s6-rc", "-u", "change", name)
    ok = result.rc == 0
    msg = result.stderr or result.stdout or f"rc={result.rc}"
    return ActionResult(ok=ok, message=msg)


@app.post("/services/{name}/stop", response_model=ActionResult)
async def service_stop(name: str) -> ActionResult:
    _require_known_service(name)
    result = await _run("s6-rc", "-d", "change", name)
    ok = result.rc == 0
    msg = result.stderr or result.stdout or f"rc={result.rc}"
    return ActionResult(ok=ok, message=msg)


# -- Backend container endpoints — PLAN §4.8 -----------------------------------
# Hybrid wiring (matches PLAN §4.8 example):
#   - pull   → docker-py client.api.pull(stream=True), SSE per layer
#   - start  → restart an existing running container, start an existing
#              stopped container, or 'docker compose up -d --no-build
#              <service>' when the container does not exist. No build is
#              attempted from the UI path; missing images are reported so the
#              user can pull/install first.
#   - stop   → docker-py container.stop(), keeping the container for reuse.
#   - restart → hard reset an existing backend, or create/start it when absent.
#   - status → docker-py images.get + containers.get


@app.post("/backends/{name}/pull")
async def backend_pull(name: str) -> StreamingResponse:
    spec = _require_known_backend(name)
    image = spec["image"]

    def generate():
        try:
            client = _docker_client()
        except DockerException as e:
            payload = json.dumps({"message": f"docker init failed: {e}"})
            yield f"event: error\ndata: {payload}\n\n"
            return

        try:
            for chunk in client.api.pull(image, stream=True, decode=True):
                yield f"data: {json.dumps(chunk)}\n\n"
        except DockerException as e:
            payload = json.dumps({"image": image, "message": str(e)})
            yield f"event: error\ndata: {payload}\n\n"
            return

        # Verify the image is actually present after the pull stream ends —
        # the daemon sometimes ends the stream on a 'manifest unknown' error
        # without raising on the iterator side.
        try:
            client.images.get(image)
            done = json.dumps({"image": image, "ok": True})
            yield f"event: done\ndata: {done}\n\n"
        except ImageNotFound:
            payload = json.dumps(
                {"image": image, "message": "pull stream ended but image missing"}
            )
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/backends/{name}/start", response_model=ActionResult)
async def backend_start(name: str) -> ActionResult:
    spec = _require_known_backend(name)
    return await _ensure_backend_running(name, spec)


@app.post("/backends/{name}/restart", response_model=ActionResult)
async def backend_restart(name: str) -> ActionResult:
    spec = _require_known_backend(name)
    return await _ensure_backend_running(name, spec)


@app.post("/backends/{name}/stop", response_model=ActionResult)
async def backend_stop(name: str) -> ActionResult:
    spec = _require_known_backend(name)
    container_name = spec["container"]

    def _stop_existing() -> tuple[bool, str]:
        try:
            client = _docker_client()
        except DockerException as e:
            return False, f"docker init failed: {e}"
        try:
            ctr = client.containers.get(container_name)
        except NotFound:
            return True, f"{container_name} was not created"
        except DockerException as e:
            return False, f"inspect failed: {e}"
        try:
            state = _container_raw_state(ctr)
            if state == "paused":
                ctr.unpause()
                state = "running"
            if state != "running":
                return True, f"{container_name} already stopped ({state})"
            ctr.stop(timeout=10)
            return True, f"{container_name} stopped"
        except DockerException as e:
            return False, f"stop failed: {e}"

    ok, msg = await asyncio.to_thread(_stop_existing)
    return ActionResult(ok=ok, message=msg)


async def _ensure_backend_running(name: str, spec: Dict[str, str]) -> ActionResult:
    """Start policy backend without building; reset if it is already running."""

    container_name = spec["container"]

    def _start_or_restart_existing() -> tuple[Optional[bool], str]:
        try:
            client = _docker_client()
        except DockerException as e:
            return False, f"docker init failed: {e}"
        try:
            ctr = client.containers.get(container_name)
        except NotFound:
            return None, "not_created"
        except DockerException as e:
            return False, f"inspect failed: {e}"

        try:
            state = _container_raw_state(ctr)
            if state == "paused":
                ctr.unpause()
                state = "running"
            if state == "running":
                ctr.restart(timeout=10)
                return True, f"{container_name} restarted"
            ctr.start()
            return True, f"{container_name} started from {state}"
        except DockerException as e:
            return False, f"start/restart failed: {e}"

    handled, msg = await asyncio.to_thread(_start_or_restart_existing)
    if handled is not None:
        return ActionResult(ok=handled, message=msg)

    # Container is absent. Pre-flight the image so compose up never starts an
    # implicit pull/build path from a simple ON click.
    def _find_local_image() -> Optional[str]:
        try:
            return _local_backend_image(_docker_client(), spec)
        except DockerException:
            return None

    local_image = await asyncio.to_thread(_find_local_image)
    if not local_image:
        images = ", ".join(_backend_image_candidates(spec))
        raise HTTPException(
            409,
            f"No local image for {name}. Expected one of: {images}. "
            f"Connect internet and call /backends/{name}/pull first.",
        )

    cmd = _compose_base_cmd() + ["up", "-d", "--no-build", spec["service"]]
    result = await _run(*cmd, timeout=60.0)
    ok = result.rc == 0
    msg = result.stderr or result.stdout or f"rc={result.rc}"
    if ok:
        msg = f"{spec['container']} created/started using local image {local_image}. {msg}"
    return ActionResult(ok=ok, message=msg)


@app.get("/backends/{name}/status", response_model=BackendStatus)
async def backend_status(name: str) -> BackendStatus:
    spec = _require_known_backend(name)

    def _inspect():
        client = _docker_client()
        pulled = _local_backend_image(client, spec) is not None
        try:
            ctr = client.containers.get(spec["container"])
        except NotFound:
            return pulled, "not_created", None, None, []
        except DockerException as e:
            raise HTTPException(500, f"docker inspect failed: {e}")
        raw = _container_raw_state(ctr)
        if raw == "running":
            mapped = "running"
        elif raw in ("exited", "dead", "created", "paused"):
            mapped = "exited"
        else:
            mapped = "unknown"
        services = _backend_service_statuses(ctr, raw)
        return pulled, mapped, ctr.id, raw, services

    pulled, container_state, container_id, raw, services = await asyncio.to_thread(_inspect)
    return BackendStatus(
        name=name,
        image=spec["image"],
        image_pulled=pulled,
        container_state=container_state,
        container_id=container_id,
        raw_state=raw,
        services=services,
    )
