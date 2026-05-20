#!/usr/bin/env python3
"""Camera-name alias resolution for GR00T runtime inputs."""

from __future__ import annotations

import re
from typing import Dict, Iterable


_CAMERA_SEMANTIC_RE = re.compile(
    r"^cam_(?P<a>left|right|head|wrist)_(?P<b>left|right|head|wrist)$"
)


def resolve_camera_mappings(
    robot_camera_names: Iterable[str],
    policy_video_keys: Iterable[str],
) -> Dict[str, str]:
    """Map RobotClient camera names to GR00T policy video keys.

    The canonical Cyclo camera names are ``cam_<part>_<side>`` such as
    ``cam_head_left``. Some older runtime configs or checkpoints may still
    expose ``rgb.`` prefixes or ``cam_<side>_<part>`` ordering. Treat those
    as inference-time aliases while keeping exact matches preferred.
    """
    camera_names = list(robot_camera_names)
    policy_keys = set(policy_video_keys)
    if not policy_keys:
        return {cam: cam for cam in camera_names}

    active: Dict[str, str] = {}
    used_policy_keys = set()
    for cam in camera_names:
        candidates = camera_policy_key_candidates(cam)
        matches = sorted(policy_keys & candidates)
        if not matches:
            continue

        if cam in matches:
            chosen = cam
        elif len(matches) == 1:
            chosen = matches[0]
        else:
            raise RuntimeError(f"Ambiguous camera mapping for {cam}: matches {matches}")

        if chosen in used_policy_keys:
            raise RuntimeError(
                f"Policy camera key {chosen} matched multiple robot cameras"
            )
        active[cam] = chosen
        used_policy_keys.add(chosen)

    missing = sorted(policy_keys - used_policy_keys)
    if missing:
        raise RuntimeError(
            "Missing camera mappings for policy video keys: "
            f"{missing}; robot has {camera_names}; matched {active}"
        )
    return active


def camera_policy_key_candidates(camera_name: str) -> set[str]:
    aliases = {camera_name}
    parts = camera_name.split(".")
    suffix = parts[-1]
    prefixes = parts[:-1]
    aliases.add(suffix)

    semantic_names = {suffix}
    match = _CAMERA_SEMANTIC_RE.match(suffix)
    if match:
        first = match.group("a")
        second = match.group("b")
        side = first if first in {"left", "right"} else second
        part = first if first in {"head", "wrist"} else second
        if side in {"left", "right"} and part in {"head", "wrist"}:
            semantic_names.add(f"cam_{side}_{part}")
            semantic_names.add(f"cam_{part}_{side}")

    for name in semantic_names:
        aliases.add(name)
        aliases.add(f"rgb.{name}")
        if prefixes:
            aliases.add(".".join([*prefixes, name]))

    return aliases
