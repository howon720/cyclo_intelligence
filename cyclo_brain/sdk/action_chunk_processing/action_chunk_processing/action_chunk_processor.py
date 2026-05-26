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

"""Action list post-processing pipeline."""

from __future__ import annotations

import collections
import threading
from typing import Dict, List, Optional

import numpy as np


class ActionChunkProcessor:

    BLEND_DURATION_S = 0.2

    def __init__(
        self,
        inference_hz: float = 15.0,
        control_hz: float = 100.0,
        chunk_align_window_s: float = 0.3,
        postprocess: bool = True,
        target_chunk_size: Optional[int] = None,
        alignment_mode: str = "l2",
    ):
        self._inference_hz = float(inference_hz)
        self._control_hz = float(control_hz)
        self._chunk_align_window_s = max(0.0, float(chunk_align_window_s))
        self._blend_steps = max(1, int(self.BLEND_DURATION_S * self._control_hz))
        self._postprocess = bool(postprocess)
        self._target_chunk_size = target_chunk_size
        self._alignment_mode = str(alignment_mode).lower()
        if self._target_chunk_size is not None and self._target_chunk_size <= 0:
            raise ValueError("target_chunk_size must be positive")
        if self._alignment_mode not in {"l2", "none", "rtc"}:
            raise ValueError("alignment_mode must be one of: 'l2', 'none', 'rtc'")

        self._buffer: collections.deque = collections.deque()
        self._last_action: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    @property
    def buffer_size(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def last_action(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._last_action is None else self._last_action.copy()

    @property
    def output_hz(self) -> float:
        return self._control_hz if self._postprocess else self._inference_hz

    def push_chunk(self, chunk: np.ndarray) -> int:
        if chunk.ndim != 2:
            raise ValueError(f"chunk must be 2D (T, D); got shape {chunk.shape}")

        with self._lock:
            if not self._postprocess:
                for action in chunk:
                    self._buffer.append(np.asarray(action).copy())
                if len(chunk) > 0:
                    self._last_action = np.asarray(chunk[-1]).copy()
                return len(chunk)

            aligned = self._align(chunk)
            if len(aligned) == 0:
                return 0

            interpolated = self._interpolate(aligned)
            blended = self._blend(interpolated)

            for action in blended:
                self._buffer.append(action)

            if len(blended) > 0:
                self._last_action = blended[-1].copy()
            return len(blended)

    def push_actions(self, actions: np.ndarray) -> int:
        return self.push_chunk(actions)

    def pop_action(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._buffer:
                return self._buffer.popleft()
            return None if self._last_action is None else self._last_action.copy()

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._last_action = None

    def _align(self, chunk: np.ndarray) -> np.ndarray:
        if self._alignment_mode == "none":
            return chunk
        if self._alignment_mode == "rtc":
            return self._rtc_align(chunk)
        return self._l2_align(chunk)

    def _l2_align(self, chunk: np.ndarray) -> np.ndarray:
        if self._last_action is None or len(chunk) <= 1:
            return chunk
        search_n = int(round(self._chunk_align_window_s * self._inference_hz))
        search_n = max(1, min(search_n, len(chunk)))
        distances = np.linalg.norm(chunk[:search_n] - self._last_action, axis=1)
        best_idx = int(np.argmin(distances))
        start_idx = best_idx + 1
        if start_idx >= len(chunk):
            return chunk[:0]
        return chunk[start_idx:]

    def _rtc_align(self, chunk: np.ndarray) -> np.ndarray:
        raise NotImplementedError("RTC alignment is not implemented yet")

    def _interpolate(self, chunk: np.ndarray) -> np.ndarray:
        T, D = chunk.shape
        if self._target_chunk_size is not None:
            target = int(self._target_chunk_size)
            if T == target:
                return chunk
            if T == 1:
                return np.repeat(chunk, target, axis=0)
            t_original = np.linspace(0.0, 1.0, T)
            t_interp = np.linspace(0.0, 1.0, target)
            out = np.empty((target, D))
            for d in range(D):
                out[:, d] = np.interp(t_interp, t_original, chunk[:, d])
            return out
        if T < 2:
            return chunk
        t_original = np.arange(T) / self._inference_hz
        duration = (T - 1) / self._inference_hz
        n_interp = int(round(duration * self._control_hz)) + 1
        t_interp = np.linspace(0, duration, n_interp)
        out = np.empty((n_interp, D))
        for d in range(D):
            out[:, d] = np.interp(t_interp, t_original, chunk[:, d])
        return out

    def _blend(self, chunk: np.ndarray) -> np.ndarray:
        if self._last_action is None or len(chunk) == 0:
            return chunk
        chunk = chunk.copy()
        n_blend = min(self._blend_steps, len(chunk))
        for i in range(n_blend):
            alpha = (i + 1) / (n_blend + 1)
            chunk[i] = (1 - alpha) * self._last_action + alpha * chunk[i]
        return chunk


def build_action_joint_map(
    action_keys: List[str],
    joint_order: Dict[str, List[str]],
) -> Dict[str, str]:
    action_joint_map: Dict[str, str] = {}
    for key in action_keys:
        leader_key = f"joint_order.leader_{key}"
        if leader_key in joint_order:
            action_joint_map[key] = leader_key
    return action_joint_map


def split_action(
    action: np.ndarray,
    action_joint_map: Dict[str, str],
    joint_order: Dict[str, List[str]],
) -> Dict[str, np.ndarray]:
    result: Dict[str, np.ndarray] = {}
    offset = 0
    for _modality_key, leader_group in action_joint_map.items():
        joint_names = joint_order.get(leader_group, [])
        n_joints = len(joint_names)
        if n_joints == 0:
            continue
        values = action[offset:offset + n_joints]
        offset += n_joints
        publisher_key = leader_group.removeprefix("joint_order.")
        result[publisher_key] = values
    return result
