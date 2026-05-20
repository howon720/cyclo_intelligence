# Trained LeRobot Model Inventory

This document lists only the checkpoints completed during this validation cycle. The other 6 policies failed during training; see `TRAINING_RESULTS.md` for details.

## 1. ACT: Action Chunking Transformer

| Item | Value |
|---|---|
| **Policy type** | `act` |
| **Steps** | 5,000 |
| **Job name** | `act_task0013_5000` |
| **Dataset** | `Dongkkka/Task_0013_clean_cafe_table_paper_lerobot` (after v3.0 conversion) |
| **Robot type** | `ffw_sg2_rev1` |
| **Training time** | 3h 13m (Jetson Orin, 2.31s/step) |
| **batch_size** | 8 |
| **Learnable params** | 51,644,310 (~52M) |
| **Backbone** | ResNet18 (per-camera) |

### Host Path

```text
docker/workspace/training_outputs/act_task0013_5000/
└── checkpoints/
    ├── 005000/
    │   ├── pretrained_model/                                        (198 MB)
    │   │   ├── config.json
    │   │   ├── model.safetensors                                    207 MB
    │   │   ├── policy_preprocessor.json                             ← v3.0 new format
    │   │   ├── policy_preprocessor_step_3_normalizer_processor.safetensors
    │   │   ├── policy_postprocessor.json
    │   │   ├── policy_postprocessor_step_0_unnormalizer_processor.safetensors
    │   │   └── train_config.json
    │   └── training_state/                                          (395 MB)
    │       ├── optimizer_state.safetensors                          413 MB
    │       ├── optimizer_param_groups.json
    │       ├── rng_state.safetensors
    │       └── training_step.json
    └── last → 005000   (symlink)
```

### Container Path for LOAD `model_path`

```text
/workspace/training_outputs/act_task0013_5000/checkpoints/last
or
/workspace/training_outputs/act_task0013_5000/checkpoints/005000
or
/workspace/training_outputs/act_task0013_5000/checkpoints/005000/pretrained_model
```

The engine's `_resolve_model_dir` automatically descends into `pretrained_model/`.

### Input/Output Spec Excerpt from `config.json`

**Inputs**:
- `observation.state` shape = (36,)
- `observation.images.rgb.cam_left_head`  shape = (720, 1280, 3)
- `observation.images.rgb.cam_right_head` shape = (720, 1280, 3)
- `observation.images.rgb.cam_left_wrist` shape = (640, 480, 3)
- `observation.images.rgb.cam_right_wrist` shape = (640, 480, 3)

**Output modalities (`action_keys`)**:
- `arm_left`, `arm_right`, `head`, `lift`, `mobile`

### Disk Usage

- Deploying only `pretrained_model/`: 198 MB
- Including `training_state/` for resume: 593 MB

---

## 2. SmolVLA: Vision-Language-Action

| Item | Value |
|---|---|
| **Policy type** | `smolvla` |
| **Steps** | 500 |
| **Job name** | `smolvla_500_v2` |
| **Dataset** | `Dongkkka/Task_0013_clean_cafe_table_paper_lerobot` (v3.0) |
| **Robot type** | `ffw_sg2_rev1` |
| **Training time** | About 14 minutes (Jetson Orin) |
| **batch_size** | 8 |
| **Extra option** | `--policy.max_state_dim=36` (raised from default 32 to 36) |
| **Backbone** | SmolVLM (Vision-Language base) |

### Host Path

```text
docker/workspace/training_outputs/smolvla_500_v2/
└── checkpoints/
    ├── 000500/
    │   ├── pretrained_model/                                        (1.2 GB)
    │   │   ├── config.json
    │   │   ├── model.safetensors
    │   │   ├── policy_preprocessor.json + .safetensors
    │   │   ├── policy_postprocessor.json + .safetensors
    │   │   └── train_config.json
    │   └── training_state/                                          (394 MB)
    └── last → 000500
```

### LOAD Validation

Calling LOAD on the Zenoh service `/lerobot/inference_command` returned the same action keys: `arm_left, arm_right, head, lift, mobile`.

500 steps is not enough for imitation-quality validation; full training would need tens of thousands of steps. The purpose of this validation was to confirm that the training pipeline is compatible with the ffw dataset and that the LOAD path works with SmolVLA.

---

## Other Policies: Training Not Completed

| Policy | Attempts | Final Reason |
|---|---|---|
| diffusion | 3 attempts (v3, v4, v5) | Image shape mismatch; all cameras are required to have the same shape, and `validate_features()` checks native shapes |
| multi_task_dit | 3 attempts | Same as diffusion |
| wall_x | 3 attempts | Even after raising state/action dimension caps, internal hardcoded computation produced a negative tensor dimension; pretrained access also returned 401 |
| xvla | 2 attempts (v4 fine-tune) | exit 137 (OOM); Jetson Orin memory limit |
| pi0 | 1 attempt | exit 124 (2h timeout); step rate was too slow on Jetson |
| pi05 | 1 attempt | Same as pi0 |
| sac | 1 attempt | `SACPolicy.__init__()` API mismatch inside lerobot |

**Jetson Orin is fundamentally unsuitable for VLA training.** VLA fine-tuning should run on a separate GPU server such as A100/H100.

---

_Updated: 2026-05-09 PM. Inventory of two checkpoints: ACT 5000 steps and smolvla 500 steps._
