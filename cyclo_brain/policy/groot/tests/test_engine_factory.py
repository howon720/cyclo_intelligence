#!/usr/bin/env python3
"""Tests for the GR00T Engine-process factory contract."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
INFERENCE_ENGINE = ROOT / "cyclo_brain/policy/groot/runtime/inference_engine.py"


def _install_stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class GR00TEngineFactoryTests(unittest.TestCase):
    def setUp(self):
        self._saved_modules = dict(sys.modules)

        _install_stub(
            "cv2",
            ROTATE_90_CLOCKWISE=0,
            ROTATE_180=1,
            ROTATE_90_COUNTERCLOCKWISE=2,
        )
        _install_stub("torch", inference_mode=lambda: None)
        _install_stub("gr00t")
        _install_stub("gr00t.model")
        _install_stub("gr00t.data")
        _install_stub(
            "gr00t.data.embodiment_tags",
            EmbodimentTag=types.SimpleNamespace(NEW_EMBODIMENT="new_embodiment"),
        )
        _install_stub("gr00t.policy")
        _install_stub("gr00t.policy.gr00t_policy", Gr00tPolicy=object)
        _install_stub("robot_client", RobotClient=object)
        _install_stub(
            "robot_client.camera_mapping",
            resolve_camera_feature_sources=lambda *_args, **_kwargs: {},
        )
        _install_stub("scripts")
        _install_stub("scripts.deployment")
        _install_stub(
            "scripts.deployment.standalone_inference_script",
            replace_dit_with_tensorrt=lambda *_args, **_kwargs: None,
        )
        _install_stub(
            "scripts.deployment.export_onnx_n1d7",
            DiTInputCapture=object,
            export_dit_to_onnx=lambda *_args, **_kwargs: None,
        )

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self._saved_modules)

    def test_runtime_module_exposes_create_engine_factory(self):
        spec = importlib.util.spec_from_file_location(
            "groot_runtime_inference_engine_under_test",
            INFERENCE_ENGINE,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        engine = module.create_engine()

        self.assertIsInstance(engine, module.GR00TInference)


if __name__ == "__main__":
    unittest.main()
