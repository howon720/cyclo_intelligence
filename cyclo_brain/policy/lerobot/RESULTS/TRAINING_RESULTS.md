# LeRobot Policy Training Validation Results

Dataset: `Dongkkka/Task_0013_clean_cafe_table_paper_lerobot`
Environment: Jetson Orin (ARM64), `dustynv/lerobot:r36.4-cu128-24.04` base, robot_type=`ffw_sg2_rev1`
Training command: `lerobot-train --policy.type=<P> --tolerance_s=0.1 --steps=<N> --policy.push_to_hub=false --wandb.enable=false`

## Dataset Characteristics That Affect Compatibility

- 50 episodes / 56,630 frames / 15 fps
- 4 cameras (`rgb.cam_<side>_<part>`):
  - `rgb.cam_left_head`, `rgb.cam_right_head`: **720×1280 (ZED stereo)**
  - `rgb.cam_left_wrist`, `rgb.cam_right_wrist`: **640×480 (RealSense)**
- State dimension = **36** (both arms 7+1×2 + head 2 + lift 1 + base 3 + EE pose 7×2)
- Action dimension = 5 modalities (`arm_left, arm_right, head, lift, mobile`)
- One task text label: `clean_cafe_table_paper`
- v2.1 format must be converted to v3.0 using the conversion script
- After conversion, `task_index` had orphan indices [0, 1, 2], which must be sanitized to 0

## Results by Policy

| Policy | Steps | Result | Time | Notes |
|---|---|---|---|---|
| **act** | 5,000 | ✅ **SUCCESS** | 3h 13m (2.31s/step) | Previous session; valid checkpoint created |
| **diffusion** | 500 | ❌ FAIL | 21s | Requires identical image shapes |
| **multi_task_dit** | 500 | ❌ FAIL | 22s | Requires identical image shapes |
| **wall_x** | 500 | ❌ FAIL | 21s | state dim 36 > max 20 |
| **xvla** | 500 | ❌ FAIL | 22s | From-scratch training unsupported; `vision_config` required |
| **smolvla** | 500 | ❌ FAIL | 1m 17s | state dim 36 != 32 |
| **pi0** | 500 | ❌ FAIL | 4m 16s | HF gated repo 401 (no token) |
| **sac** | 500 | ❌ FAIL | 22s | `SACPolicy.__init__()` API mismatch |

## Failure Analysis

### 1. Camera Image Shape Mismatch (diffusion, multi_task_dit)

```text
ValueError: Image 'observation.images.rgb.cam_left_wrist' shape (640, 480, 3)
         != 'observation.images.rgb.cam_left_head' shape (720, 1280, 3)
```

- These policies require **all cameras to have the same resolution** because they share a single encoder.
- ACT uses a separate ResNet18 encoder per camera, so mixed resolutions are acceptable.
- **Fix**: Upsample wrist cameras to 720×1280, or downsample all cameras to the same smaller resolution such as 224×224 and re-upload. Another option is to force `image_resize=(H,W)` in the policy config.

### 2. State Dimension Too Large (wall_x, smolvla)

```text
[wall_x]   ValueError: State dimension 36 exceeds max_state_dim 20.
[smolvla]  RuntimeError: tensor (32) must match (36) at dim 1.
```

- Some VLA backbones have hard caps on token count or projection dimension (`wall_x=20`, `smolvla=32`).
- The ffw_sg2 state dimension is 36, including 14 dimensions of EE pose, so it exceeds those caps.
- **Fix**: Reduce the state dimension to 21-32 dimensions (removing EE pose gives 22), or raise `max_state_dim` or an equivalent policy option when the policy supports it.

### 3. Pretrained Backbone Dependency (xvla)

```text
ValueError: vision_config is required
```

- xvla expects a vision backbone config or a pretrained checkpoint (`--policy.path=<HF_id>`) as the fine-tuning base.
- From-scratch training is unsupported.
- **Fix**: Retry fine-tuning with a pretrained xvla checkpoint from the HF Hub as the base.

### 4. HF Gated Repo (pi0)

```text
OSError: You are trying to access a gated repo.
401 Client Error.
```

- pi0 pretrained weights such as `lerobot/pi0_base` require an approved Hugging Face token after access is requested.
- **Fix**: Request HF access and inject `HF_TOKEN` into the container environment.

### 5. SAC API Mismatch

```text
TypeError: SACPolicy.__init__() got an unexpected keyword argument 'dataset_stats'
```

- This failed before RL data compatibility was evaluated because of an internal lerobot API mismatch.
- Other policies such as ACT and Diffusion use a unified signature with `dataset_stats=...`, but SACPolicy has not been updated.
- **Fix**: Patch lerobot upstream or bypass the CLI and call the policy class directly.

## Conclusion

**With the current dataset (`Dongkkka/Task_0013...`), ACT is the only policy that can train from scratch.** Other policies need one or more of the following:

1. Normalize camera resolutions across all 4 cameras.
2. Reduce state dimension (`wall_x <= 20`, `smolvla <= 32`; `multi_task_dit` needs further verification).
3. Download pretrained checkpoints and provide `HF_TOKEN` for pi0, or use fine-tuning mode for xvla.

## Follow-up Improvements

- [ ] Retry diffusion/multi_task_dit after normalizing dataset image shapes.
- [ ] Create a reduced-state dataset version with EE pose removed, then retry smolvla/wall_x.
- [ ] Register `HF_TOKEN`, then retry pi0/pi05 fine-tuning.
- [ ] Retry xvla fine-tuning with a pretrained base through the `policy.path` option.

---

## Second Attempt (2026-05-09 Early Morning to Morning)

Retried after applying fixes for each failure cause (Phase A v2-v5).

### Improved Results

| Policy | Attempt | Result | Notes |
|---|---|---|---|
| **smolvla** | `--policy.max_state_dim=36` | ✅ **SUCCESS** (500 steps, about 14 minutes) | LOAD validation also passed, with the exact 5 action_keys |
| diffusion (v3) | `--policy.resize_shape 84 84` (space-form) | ❌ argparse error | draccus requires the `=[a,b]` format |
| diffusion (v4) | `--policy.resize_shape=[84,84]` | ❌ shape mismatch | Resize is applied, but `validate_features()` checks native shape first |
| multi_task_dit (v4) | `--policy.image_resize_shape=[84,84]` | ❌ shape mismatch | Same cause |
| wall_x (v3) | `--policy.max_state_dim=36` | ❌ action_dim 22 > 20 | Progressed to the next failure |
| wall_x (v3) | `--policy.max_state_dim=36 --policy.max_action_dim=22` | ❌ negative tensor dim | Internal hardcoded computation broke |
| **xvla** | `--policy.path=lerobot/xvla-base` (fine-tune) | ❌ exit 137 (OOM) | Jetson Orin memory limit |
| **pi0** | `--policy.path=lerobot/pi0_base` (fine-tune) | ❌ exit 124 (2h timeout) | Step rate was too slow |
| **pi05** | `--policy.path=lerobot/pi05_base` (fine-tune) | ❌ exit 124 (2h timeout) | Same as pi0 |

### Phase v5: Dataset Preprocessing

- Built a new dataset with all cameras normalized to 240×320 (ffmpeg AV1 encoding, about 13 minutes).
- Location: `/root/.cache/huggingface/lerobot/Dongkkka/Task_0013_clean_cafe_table_paper_lerobot_uniform/`
- Added `--dataset.root=$DATASET_ROOT` to the training command.
- In progress: retrying diffusion / multi_task_dit / wall_x (max=64/32).

## Key Findings

### Jetson Orin Is Not Suitable for VLA Training

- xvla / pi0 / pi05 all failed with OOM or timeout. Jetson Orin with 64GB shared memory is not enough for fine-tuning VLA backbones such as Gemma-2B with batch=8.
- Conclusion: run VLA training on a separate GPU server such as A100/H100. Use Jetson for inference only.

### dustynv-build vs lerobot-build Mismatch

- The dustynv/lerobot base image had `/etc/pip.conf` pointing to a dead domain (`pypi.jetson-ai-lab.dev`).
- Workaround: set `PIP_INDEX_URL=https://pypi.org/simple`, which has already been reflected in the Dockerfile.

### Dataset Issues

1. Camera image shapes are inconsistent (head 720×1280 / wrist 640×480), so policies that enforce `validate_features()` reject the dataset (diffusion, multi_task_dit, wall_x).
2. The dataset is v2.1 and, after v3.0 conversion, had orphan `task_index` values [0, 1, 2], so sanitization is required.
3. State dimension 36 exceeds hardcoded caps in some VLAs (`smolvla` 32, `wall_x` 20), though `max_state_dim` can solve it for policies that support the option.

---

_Updated: 2026-05-09 early morning to morning. ACT (5000 steps) and smolvla (500 steps) trained successfully. Three VLA policies (xvla/pi0/pi05) failed because of Jetson limits. Phase v5 (uniform dataset) was in progress._
