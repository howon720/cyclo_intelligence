# LeRobot Policy Validation Results (2026-05-08 to 2026-05-09)

This directory contains LeRobot policy training/inference validation results using the ffw_sg2 dataset (`Dongkkka/Task_0013_clean_cafe_table_paper_lerobot`).

## Files

| File | Content |
|---|---|
| [TRAINING_RESULTS.md](TRAINING_RESULTS.md) | Results from 7 policy training attempts and failure analysis (Phase v1-v5) |
| [TRAINED_MODELS.md](TRAINED_MODELS.md) | Inventory of completed checkpoints (ACT 5000 steps + smolvla 500 steps) |
| [INFERENCE_TEST_RESULTS.md](INFERENCE_TEST_RESULTS.md) | LOAD validation for 9 checkpoints (2 local + 7 HF pretrained) |

## TL;DR: Final Summary

### Trained Models (2)

| Policy | Steps | Result |
|---|---|---|
| **ACT** | 5,000 | ✅ Full training + LOAD passed, exact action_keys |
| **SmolVLA** | 500 | ✅ Training + LOAD passed, but the step count is too low for quality validation |

### LOAD Validation (9)

| Result | Cases |
|---|---|
| ✅ FULL SUCCESS (2) | ACT_5000, smolvla_500_v2: checkpoints trained by us |
| 🟡 PARTIAL (4) | xvla-base, xvla-libero, xvla-widowx, smolvla_base: weights loaded, but IO mapping mismatched because ffw cameras differ from the policy training environments |
| ❌ FAIL (3) | pi0_base (Jetson timeout), pi05_base (old format), diffusion_pusht (old format) |

### Key Infrastructure Fixes (2026-05-08 to 2026-05-09 Cycle)

1. Switched to the **dustynv/lerobot base image** to avoid missing Jetson cu126 cp312 wheels.
2. Added **`PIP_INDEX_URL=pypi.org/simple`** to the Dockerfile to bypass the dead `jetson-ai-lab.dev` pip index.
3. Initialized the **lerobot submodule and zenoh_ros2_sdk submodule**, which are prerequisites for LOAD infrastructure.
4. Unified **camera naming** across data collection and robot configs as `rgb.cam_<side>_<part>` (7 files changed).
5. Fixed the **`lerobot_engine` policy_type detection bug** where HF Hub IDs fell back to ACT; all policy types can now dispatch correctly.
6. Expanded **Dockerfile extras**: `dataset, training, async, peft, diffusion, multi_task_dit, wallx, pi, smolvla, xvla, hilserl` (production excludes training extras).
7. Converted the dataset from **v2.1 to v3.0** and sanitized orphan `task_index` values.

### Jetson Orin Limitations Observed

- VLA policies (`xvla`, `pi0`, `pi05`) are effectively not trainable from scratch or fine-tunable on Jetson Orin because of OOM or timeout.
- Large VLA inference cold-load can also timeout for models like `pi0`/`pi05` (2GB+ weights, over the 600s limit).
- ACT and smolvla are feasible for both training and inference on this setup.

## Follow-up Items

- [ ] Fix Docker `HEALTHCHECK` by resolving the dynamic `s6-svstat` path.
- [ ] Start `rmw_zenohd` automatically as an s6 service, or adopt the Talos `zenoh_daemon`.
- [ ] Retry diffusion / multi_task_dit after normalizing dataset camera resolutions (the Phase v5 `_uniform` dataset can be used).
- [ ] Fine-tune xvla / pi0 on a separate GPU server (A100 class or higher), then move only the checkpoint to Jetson for inference.
- [ ] Handle old-format HF checkpoints (`pi05_base`, `diffusion_pusht`) by bypassing `make_pre_post_processors` or re-uploading them in the new format.
- [ ] Add Docker disk management through scheduled prune or an external disk mount.
- [ ] Review the separate `groot` extra path for flash-attn ARM source builds.
