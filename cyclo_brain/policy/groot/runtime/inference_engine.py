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
#
# Author: Dongyun Kim

"""GR00T N1.6 inference engine.

Encapsulates Gr00tPolicy loading, RobotClient setup, observation
preprocessing, and action chunk postprocessing. Imported by
runtime/inference_server.py (Process A) which slots it into the
cyclo_intelligence two-process pattern (LOAD srv → configure broadcast
→ Zenoh trigger/chunk).

Original Step 1 location: cyclo_brain/policy/groot/inference.py.
Moved to runtime/ as part of D10-groot (mirrors lerobot/runtime/ layout).
"""
import logging
import os
import sys
import tempfile
import time
from typing import Optional

import cv2
import numpy as np
import torch


# -- robot_client import shim --------------------------------------------------
# /robot_client_sdk/ is the bind-mount root; the package itself sits at
# /robot_client_sdk/robot_client/ so the parent dir goes onto sys.path.
_ROBOT_CLIENT_PATH = os.environ.get("ROBOT_CLIENT_SDK_PATH", "/robot_client_sdk")
if os.path.exists(_ROBOT_CLIENT_PATH) and _ROBOT_CLIENT_PATH not in sys.path:
    sys.path.insert(0, _ROBOT_CLIENT_PATH)

import gr00t.model  # noqa: F401 - register custom models
from gr00t.data.embodiment_tags import EmbodimentTag  # noqa: E402
from gr00t.policy.gr00t_policy import Gr00tPolicy  # noqa: E402
from robot_client import RobotClient  # noqa: E402


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# Add GR00T root to sys.path for deployment script imports.
# After the move into runtime/, parents[0]=runtime, parents[1]=groot,
# so the legacy in-tree fallback is a layer deeper than before — but
# the /gr00t fallback (where the submodule lives in the container)
# still wins, which is what we want at runtime.
_GROOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_GROOT_ROOT, "scripts", "deployment")):
    _groot_path = _GROOT_ROOT
elif os.path.isdir("/gr00t/scripts/deployment"):
    _groot_path = "/gr00t"
else:
    _groot_path = None

if _groot_path and _groot_path not in sys.path:
    sys.path.insert(0, _groot_path)

# TensorRT DiT acceleration - reuse existing GR00T deployment code
from scripts.deployment.standalone_inference_script import (  # noqa: E402
    replace_dit_with_tensorrt,
)
try:  # GR00T N1.7
    from scripts.deployment.export_onnx_n1d7 import (  # noqa: E402
        DiTInputCapture,
        export_dit_to_onnx,
    )
except ImportError:  # GR00T N1.6 / N1.6.1
    from scripts.deployment.export_onnx_n1d6 import (  # noqa: E402
        DiTInputCapture,
        export_dit_to_onnx,
    )


def build_trt_engine(policy: Gr00tPolicy, observation: dict, engine_path: str):
    """Export DiT to ONNX and build TensorRT engine automatically.

    Uses DiTInputCapture and export_dit_to_onnx from GR00T deployment scripts,
    then builds the TRT engine via build_tensorrt_engine.build_engine().
    Takes ~3-5 min on Orin.
    """
    from scripts.deployment.build_tensorrt_engine import (
        build_engine,
        derive_shapes_with_hint,
    )

    logger = logging.getLogger("groot_inference")
    engine_dir = os.path.dirname(engine_path)

    # Step 1: Capture DiT input shapes via hook
    logger.info("Capturing DiT input shapes...")
    capture = DiTInputCapture()
    hook = policy.model.action_head.model.register_forward_pre_hook(
        capture.hook_fn, with_kwargs=True
    )
    with torch.inference_mode():
        policy.get_action(observation)
    hook.remove()

    if not capture.captured:
        raise RuntimeError("Failed to capture DiT inputs")

    # Step 2/3: Export DiT to ONNX in an isolated temporary directory, then
    # build the TensorRT engine into the checkpoint directory. GR00T's ONNX
    # exporter consolidates external-data files by deleting every non
    # .onnx/.json/.data file next to the ONNX path. Keeping ONNX artifacts out
    # of the checkpoint directory prevents model-*.safetensors from being
    # mistaken for temporary external data.
    with tempfile.TemporaryDirectory(prefix=".trt_export_", dir=engine_dir) as export_dir:
        onnx_path = os.path.join(export_dir, "dit_model_bf16.onnx")

        # Patch torch.onnx.export to force dynamo=False (avoids torch.export
        # failures with DiT's dynamic shapes, without modifying upstream GR00T).
        _orig_export = torch.onnx.export
        def _patched_export(*args, **kwargs):
            kwargs.setdefault("dynamo", False)
            return _orig_export(*args, **kwargs)
        torch.onnx.export = _patched_export
        try:
            export_dit_to_onnx(
                policy=policy,
                captured_inputs=capture,
                output_path=onnx_path,
                use_bf16=True,
            )
        finally:
            torch.onnx.export = _orig_export

        logger.info("ONNX exported: %s", onnx_path)

        # N1.7 exports only the visual-language sequence axis as dynamic. The
        # state/action sequence (sa_embs) is static for a given policy/action
        # horizon. Derive profiles from ONNX so fixed dims stay fixed and only
        # named dynamic dims get ranges.
        vl_seq_len = int(capture.vl_embs.shape[1])
        min_shapes, opt_shapes, max_shapes = derive_shapes_with_hint(
            onnx_path,
            opt_seq_lens={"vl_seq_len": vl_seq_len},
            max_batch=1,
        )

        # Keep the previous generous upper bound for language-conditioned
        # inputs. derive_shapes_with_hint uses ~2x opt by default, which can be
        # too tight if the BT sends a longer instruction than export captured.
        for name in ("vl_embs", "image_mask", "backbone_attention_mask"):
            shape = max_shapes.get(name)
            if shape and len(shape) > 1 and shape[1] != opt_shapes[name][1]:
                widened = list(shape)
                widened[1] = max(widened[1], 512)
                max_shapes[name] = tuple(widened)

        build_engine(
            onnx_path=onnx_path,
            engine_path=engine_path,
            precision="bf16",
            workspace_mb=8192,
            min_shapes=min_shapes,
            opt_shapes=opt_shapes,
            max_shapes=max_shapes,
        )

    logger.info("Cleaned up temporary ONNX export directory")


class GR00TInference:
    """Encapsulates GR00T policy loading, observation building, and inference."""

    logger = logging.getLogger("groot_inference")
    IMAGE_SIZE = (256, 256)
    DEFAULT_EMBODIMENT_TAG = "new_embodiment"
    ROTATE_MAP = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def __init__(self):
        self.policy: Optional[Gr00tPolicy] = None
        self.robot: Optional[RobotClient] = None
        self._loaded_model_path: Optional[str] = None  # track cached policy path
        self.policy_info: dict = {
            "video": [],       # e.g. ["cam_left_head", "cam_left_wrist", ...]
            "state": [],       # e.g. ["arm_left", "arm_right"]
            "action": [],      # e.g. ["arm_left", "arm_right"]
            "language": [],    # e.g. ["annotation.human.task_description"]
        }
        self.robot_info: dict = {
            # Policy camera keys to emit in the GR00T observation.
            "cameras": [],
            # policy_camera_key -> RobotClient camera key. This lets a model
            # trained with cam_left_head consume a robot stream named
            # cam_head_left without changing the checkpoint metadata.
            "camera_sources": {},
            "joints": {},          # modality_key -> yaml_group mapping
            "camera_rotations": {},  # camera_name -> rotation_deg
        }

    @property
    def is_ready(self) -> bool:
        return self.policy is not None and self.robot is not None

    def load_policy(self, request) -> dict:
        """Load GR00T policy and create RobotClient for sensor data."""
        model_path = request.model_path
        robot_type = request.robot_type

        try:
            # Reuse cached policy if the same model is already loaded.
            # Only reload robot client (subscribers) on restart.
            if self.policy is not None and self._loaded_model_path == model_path:
                self.logger.info("Reusing cached policy: %s", model_path)
                if self.robot is not None:
                    self.robot.close()
                    self.robot = None
                self.init_policy_info()
                self.init_robot_info(robot_type)
                self.robot.wait_for_ready(timeout=10.0)
                return {
                    "success": True,
                    "message": "GR00T inference restarted (policy cached)",
                    "action_keys": list(self.policy_info["action"]),
                }

            self.logger.info("Loading GR00T policy from: %s", model_path)

            self.policy = Gr00tPolicy(
                embodiment_tag=EmbodimentTag.NEW_EMBODIMENT,
                model_path=model_path,
                device="cuda",
            )
            self._loaded_model_path = model_path

            self.init_policy_info()
            self.init_robot_info(robot_type)
            self.robot.wait_for_ready(timeout=10.0)

            # TensorRT acceleration for DiT (Action Head).
            # Keep this opt-in while validating N1.7 model-load/action flow;
            # ONNX/TRT export writes into the checkpoint directory.
            if _env_flag("GROOT_TRT_ENABLED", default=False):
                trt_path = os.path.join(model_path, "dit_model_bf16.trt")
                try:
                    if not os.path.exists(trt_path):
                        self.logger.info("No TRT engine found, building automatically...")
                        dummy_obs = self._build_dummy_observation(request.task_instruction)
                        if dummy_obs.get("success") is False:
                            raise RuntimeError(
                                f"cannot build TRT without a valid observation: "
                                f"{dummy_obs.get('message')}"
                            )
                        build_trt_engine(self.policy, dummy_obs, trt_path)

                    replace_dit_with_tensorrt(self.policy, trt_path)
                    self.logger.info("DiT accelerated with TensorRT: %s", trt_path)
                except Exception as e:
                    self.logger.warning(
                        "TensorRT acceleration unavailable, using PyTorch Eager: %s", e
                    )
            else:
                self.logger.info(
                    "TensorRT acceleration disabled (GROOT_TRT_ENABLED=%s); "
                    "using PyTorch Eager",
                    os.environ.get("GROOT_TRT_ENABLED", "unset"),
                )

            return {
                "success": True,
                "message": "GR00T inference started",
                "action_keys": list(self.policy_info["action"]),
            }
        except Exception as e:
            self.logger.error("Failed to start inference: %s", e, exc_info=True)
            return self.fail(str(e))

    def _build_dummy_observation(self, task_instruction: str = "") -> dict:
        """Build a real observation from robot sensors for TRT engine building."""
        images = self.robot.get_images(format="rgb")
        joints = self.robot.get_joint_positions()
        task = task_instruction or "dummy task"
        return self.preprocess(images, joints, task)

    def init_policy_info(self) -> None:
        """Read video/state/action/language keys from the loaded policy."""
        mc = getattr(self.policy, "modality_configs", None)
        if mc is None:
            mc = self.policy.processor.get_modality_configs().get(
                self.DEFAULT_EMBODIMENT_TAG, {}
            )

        for modality in ("video", "state", "action", "language"):
            if modality not in mc:
                self.policy_info[modality] = []
                continue
            entry = mc[modality]
            self.policy_info[modality] = getattr(
                entry, "modality_keys",
                entry.get("modality_keys", []) if isinstance(entry, dict) else [],
            )

        self.logger.info("Policy info: %s", self.policy_info)

    def init_robot_info(self, robot_type: str) -> None:
        """Create RobotClient and resolve active cameras/joints from YAML."""
        self.robot = RobotClient(robot_type)
        cam_config = self.robot._config.get("cameras", {})
        available_cameras = set(self.robot.camera_names)

        camera_sources = {}
        for policy_key in self.policy_info["video"]:
            source_key = self._resolve_camera_source(policy_key, available_cameras)
            if source_key:
                camera_sources[policy_key] = source_key

        self.robot_info["cameras"] = list(camera_sources.keys())
        self.robot_info["camera_sources"] = camera_sources
        self.robot_info["camera_rotations"] = {
            name: cfg.get("rotation_deg", 0)
            for name, cfg in cam_config.items()
            if cfg.get("rotation_deg", 0) != 0
        }

        joints = {}
        for group in self.robot.joint_group_names:
            if "follower" not in group:
                continue
            modality_key = group.removeprefix("follower_")
            if modality_key in self.policy_info["state"]:
                joints[modality_key] = group
        self.robot_info["joints"] = joints

        # Sensor-backed state modalities. Training runs have used both
        # ``mobile`` and ``odometry`` for the same 3-dim base velocity slice,
        # while robot_client keeps /odom as a sensor instead of a joint group.
        # Bridge either policy key to the odom sensor here.
        sensor_states = {}
        sensors_cfg = self.robot._config.get("sensors", {})
        if "odom" in sensors_cfg:
            for modality_key in ("mobile", "odometry"):
                if modality_key in self.policy_info["state"]:
                    sensor_states[modality_key] = "odom"
        self.robot_info["sensor_states"] = sensor_states

        self.logger.info("Robot info: %s", self.robot_info)

    def _resolve_camera_source(self, policy_key: str, available_cameras: set) -> Optional[str]:
        if policy_key in available_cameras:
            return policy_key

        explicit_aliases = {
            "cam_left_head": "cam_head_left",
            "cam_right_head": "cam_head_right",
            "cam_left_wrist": "cam_wrist_left",
            "cam_right_wrist": "cam_wrist_right",
        }
        alias = explicit_aliases.get(policy_key)
        if alias in available_cameras:
            self.logger.info("Camera alias: %s <- %s", policy_key, alias)
            return alias

        parts = policy_key.split("_")
        if len(parts) == 3 and parts[0] == "cam":
            swapped = "_".join((parts[0], parts[2], parts[1]))
            if swapped in available_cameras:
                self.logger.info("Camera alias: %s <- %s", policy_key, swapped)
                return swapped

        self.logger.warning(
            "No robot camera source matched policy camera key %s; available=%s",
            policy_key,
            sorted(available_cameras),
        )
        return None

    def get_action_chunk(self, request) -> dict:
        """Build observation from RobotClient, run inference, return action chunk."""
        if not self.is_ready:
            return self.fail("Not in inference mode")

        try:
            images = self.robot.get_images(format="rgb")
            joints = self.robot.get_joint_positions()
            task = request.task_instruction

            observation = self.preprocess(images, joints, task)
            if "success" in observation:
                return observation

            t0 = time.monotonic()
            self.logger.info("Running GR00T inference...")
            action, info = self.policy.get_action(observation)
            self.logger.info("GR00T inference completed in %.3fs", time.monotonic() - t0)
            return self.postprocess_action(action)

        except Exception as e:
            self.logger.error("Inference failed: %s", e, exc_info=True)
            return self.fail(str(e))

    def preprocess(self, images: dict, joints: dict, task: str) -> dict:
        """Build observation dict from raw sensor data. Returns fail dict on error."""
        if not images or not joints:
            return self.fail("No recent observations from sensors")

        video_obs = {}
        for cam_key in self.robot_info["cameras"]:
            source_key = self.robot_info.get("camera_sources", {}).get(cam_key, cam_key)
            img = images.get(source_key)
            if img is None:
                return self.fail(f"Missing camera: {source_key} for {cam_key}")
            rotation = self.robot_info["camera_rotations"].get(source_key)
            if rotation and rotation in self.ROTATE_MAP:
                img = cv2.rotate(img, self.ROTATE_MAP[rotation])
            video_obs[cam_key] = img[np.newaxis, np.newaxis, ...]  # (1,1,H,W,C)

        state_obs = {}
        for modality_key, yaml_group in self.robot_info["joints"].items():
            positions = joints.get(yaml_group)
            if positions is None or len(positions) == 0:
                return self.fail(f"Missing joint group: {modality_key}")
            state_obs[modality_key] = positions[np.newaxis, np.newaxis, :]  # (1,1,D)

        # Sensor-backed state modalities (e.g. mobile ← odom).
        for modality_key, sensor_name in self.robot_info.get("sensor_states", {}).items():
            if sensor_name == "odom":
                odom = self.robot.get_odom()
                if odom is None:
                    return self.fail(f"Missing sensor: {sensor_name}")
                vec = np.array([
                    float(odom["linear_velocity"][0]),
                    float(odom["linear_velocity"][1]),
                    float(odom["angular_velocity"][2]),
                ], dtype=np.float32)
                state_obs[modality_key] = vec[np.newaxis, np.newaxis, :]  # (1,1,3)
            else:
                return self.fail(f"Unsupported sensor modality: {sensor_name}")

        language_obs = {key: [[task]] for key in self.policy_info["language"]}

        return {
            "video": video_obs,
            "state": state_obs,
            "language": language_obs,
        }

    def postprocess_action(self, action: dict) -> dict:
        chunks = [
            action[key][0]  # remove batch dim
            for key in self.policy_info["action"]
            if key in action and isinstance(action[key], np.ndarray)
        ]
        if not chunks:
            return self.fail("No action output from policy")

        chunk = np.concatenate(chunks, axis=1)  # (T, D_total)
        T, D = chunk.shape
        self.logger.info("Action chunk: T=%d, D=%d", T, D)
        return {
            "success": True,
            # Keep as numpy — zenoh_ros2_sdk's publisher uses .view() for fast
            # CDR encoding and crashes on plain lists ('list' object has no
            # attribute 'view'). inference_server._publish_chunk passes this
            # straight through.
            "action_chunk": np.asarray(chunk.flatten(), dtype=np.float64),
            "chunk_size": T,
            "action_dim": D,
        }

    def cleanup(self) -> None:
        """Release robot resources. Policy is kept cached for fast restart."""
        if self.robot is not None:
            self.robot.close()
            self.robot = None

        self.policy_info = {k: [] for k in self.policy_info}
        self.robot_info = {
            "cameras": [],
            "joints": {},
            "camera_rotations": {},
        }

    @staticmethod
    def fail(message: str) -> dict:
        return {"success": False, "message": message}
