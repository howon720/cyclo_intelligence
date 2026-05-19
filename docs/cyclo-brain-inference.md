---
title: Cyclo Brain Inference Manual
description: How to prepare and run the Cyclo Brain LeRobot/GR00T inference runtime from the Cyclo Intelligence UI
---

# Cyclo Brain Inference Manual

This document explains how to run Cyclo Brain inference from the Cyclo Intelligence UI. It describes the runtime after the recent inference architecture refactor.

- Cyclo Brain policy backends run as two Python processes: `main-runtime` and `engine-process`.
- The UI selects LeRobot policy families and GR00T N1.7 from the same Inference page.
- Docker backends are controlled from the UI with `ON`, `Restart`, and `OFF`.
- Models can be selected by Hugging Face repo ID or by a local policy checkpoint path.

![Cyclo Brain Architecture](./assets/cyclo-brain-inference/05-cyclo-brain-architecture.png)

## 1. Runtime Architecture

Cyclo Brain splits each policy container into two processes.

| Process | Responsibility |
| --- | --- |
| `main-runtime` | Receives external inference commands, owns session state, pops one action at a time from the action chunk buffer, and publishes robot command topics. |
| `engine-process` | Loads the policy model, reads robot observations, and computes action chunks. It never publishes robot commands. |

The runtime flow is:

1. The UI or orchestrator sends `LOAD`, `START`, `STOP`, or `UNLOAD` style commands.
2. `main-runtime` receives the command and updates the session state.
3. `main-runtime` asks `engine-process` to load a model or compute actions through an internal Zenoh service.
4. `engine-process` reads observations, runs model inference, and returns an `action_list`.
5. `main-runtime` pushes the action chunk into `ActionChunkProcessor`, then publishes one action per control-loop tick.

LeRobot and GR00T share the same runtime contract. Only the engine-side model loading, preprocessing, and prediction implementation differs.

## 2. Open the UI

On the robot, start from the repository root:

```bash
cd /home/robotis/cyclo_intelligence
git pull
./docker/container.sh start
```

Open the UI in a browser:

```text
http://<robot-ip>/
```

For local access:

```text
http://127.0.0.1/
```

On the Home page, select the robot type first.

![Home Robot Type](./assets/cyclo-brain-inference/01-home-robot-type.png)

1. Press `Refresh Robot Type List` to load available robot configs.
2. Select the current robot in `Select Robot Type`.
3. Press `Set Robot Type` so the orchestrator and policy runtime use the same robot config.
4. Click `Inference` in the left sidebar.

## 3. Inference Page Layout

![Inference ACT Setup](./assets/cyclo-brain-inference/02-inference-act-setup.png)

The Inference page has four main areas.

| Area | Description |
| --- | --- |
| Left sidebar | Navigation: Home, Record, Training, Inference, BT Manager, Data Tools, Replay |
| Top status bar | Robot type, ROS connection state, CPU/RAM/Storage state, and inference control buttons |
| Center view | Camera images, 3D viewer, and topic monitor |
| Right Task Information panel | Model selection, Docker backend control, policy path, and inference/control settings |

## 4. Model Selection

Choose the policy backend and policy family from `Task Information > Model`.

![Model List](./assets/cyclo-brain-inference/03-inference-model-list-coming-soon.png)

The selector is grouped by backend.

### LeRobot Policies

All LeRobot entries use the same `lerobot_server` backend. The selected `Policy Path` must point to a checkpoint that matches the chosen policy family.
If the dataset was recorded with matching camera keys, state dimensions, and processor statistics, the same backend flow can run different LeRobot policy families without changing the Docker runtime.

| Model | Backend | Description |
| --- | --- | --- |
| `ACT` | `lerobot_server` | Loads a LeRobot ACT checkpoint. It does not use task instructions. |
| `SmolVLA` | `lerobot_server` | Loads a LeRobot SmolVLA checkpoint. It uses task instructions. |
| `XVLA` | `lerobot_server` | Loads a LeRobot XVLA checkpoint. It uses task instructions. |
| `Pi0` | `lerobot_server` | Loads a LeRobot Pi0 checkpoint. It uses task instructions. |
| `Pi0.5` | `lerobot_server` | Loads a LeRobot Pi0.5 checkpoint. It uses task instructions. |
| `Diffusion` | `lerobot_server` | Loads a LeRobot Diffusion checkpoint. It does not use task instructions. |

### GR00T Policies

| Model | Backend | Description |
| --- | --- | --- |
| `GR00T N1.7` | `groot_server` | Loads an NVIDIA Isaac GR00T N1.7 checkpoint. It uses task instructions. |

### Coming Soon

The following models are visible in the UI but disabled until their runtime path is validated.

- `GreenVLA`
- `OpenPI`
- `RLDX-1`

## 5. Docker Backend Control

After selecting a model, the matching Docker backend control appears in the right panel.

| Button | Behavior |
| --- | --- |
| `ON` | Creates/starts the policy container from a local image if it does not exist. Starts a stopped container. Restarts an already running container to reset the runtime. |
| `Restart` | Restarts the policy container. Use this after model-load failures, stale services, or CUDA memory cleanup needs. |
| `OFF` | Stops the policy container. It does not delete the container or image. |

Status badges:

| State | Meaning |
| --- | --- |
| `Running` | The Docker container is running. |
| `Stopped` | The container exists but is not running. |
| `Not created` | The container has not been created yet. Press `ON` to create it when the local image exists. |
| `Image missing` | The required Docker image is not available locally. Pull or install the image first. |
| `Warming up` | The container is running, but runtime process readiness is still being checked. |

Two process states are shown.

| Process | `Up` means | If `Down` or `Unknown` |
| --- | --- | --- |
| `Main` | The `main-runtime` s6 service is alive. It handles external inference commands and the control loop. | Press `Restart` to bring the backend up again. |
| `Engine` | The `engine-process` s6 service is alive. It handles model loading and action chunk inference. | Model load or inference service calls may fail; use `Restart` first. |

`Main Up` and `Engine Up` only mean the processes are alive. They do not mean a model is already loaded. Model loading happens after pressing `Start`, using the current `Policy Path`.

## 6. LeRobot Policy Inference Flow

LeRobot policies share the same Docker backend and runtime structure.

![Inference LeRobot Setup](./assets/cyclo-brain-inference/02-inference-act-setup.png)

1. Select one policy under the `LeRobot` group in `Model`.
2. Check that `LeRobot Docker` is `Running`.
3. Check that both `Main` and `Engine` are `Up`.
4. Enter a checkpoint path that matches the selected policy family in `Policy Path`.

ACT example Hugging Face repo ID:

```text
Dongkkka/Act_test_20k
```

ACT example local checkpoint path:

```text
/policy_checkpoints/lerobot/Act_test_20k
```

5. For language-conditioned LeRobot policies, enter the task command in `Task Instruction`.
6. Set `Inference Hz` to match the model action generation rate. The ACT test model usually uses `15`.
7. Set `Control Hz` to match the robot command publish rate. The default is `100`.
8. `Max Skip Ahead (s)` is the time window where the chunk aligner may skip ahead. Start with the default `0.3`.
9. Press the top `Start` button.
10. When the status changes from `Loading model...` to `Inferencing`, action publishing has started.
11. Press `Stop` to pause. The model remains loaded for pause/resume.
12. Press `Clear` to fully stop inference and clear the model/session/buffer state.

ACT and Diffusion do not use task instructions, so the `Task Instruction` input is hidden for those policy families. SmolVLA, XVLA, Pi0, and Pi0.5 use task instructions.
For every LeRobot policy, the checkpoint's `config.json` and saved processors define the expected observation schema. If a model was trained from correctly recorded Cyclo data, camera mapping and normalization are loaded from the checkpoint at runtime.

![Inference LeRobot Instruction](./assets/cyclo-brain-inference/06-inference-lerobot-smolvla-instruction.png)

## 7. GR00T N1.7 Inference Flow

GR00T uses the GR00T backend.

![Inference GR00T Setup](./assets/cyclo-brain-inference/04-inference-groot-instruction.png)

1. Select `N1.7` under the `GR00T` group in `Model`.
2. Check that `GR00T Docker` is `Running`.
3. Check that both `Main` and `Engine` are `Up`.
4. Enter a natural-language task instruction in `Task Instruction`.
5. Enter the model path in `Policy Path`.

Example Hugging Face repo ID:

```text
Dongkkka/cyclo_intelligence_groot_n1.7_model
```

Example local checkpoint path:

```text
/policy_checkpoints/groot/cyclo_intelligence_groot_n1.7_model
```

6. Press `Start`.
7. To update the task instruction during inference, edit the text and press `Update Task Instruction`.
8. Use `Stop` to pause and `Clear` to fully unload/clear the session.

GR00T N1.7 currently runs in PyTorch eager mode by default in the UI path. TensorRT action-head execution is a separate validation path; the UI default prioritizes stable runtime behavior.

## 8. Inference Control Buttons

The top `Inference` control bar is shared by all backends.

| Button | Shortcut | Description |
| --- | --- | --- |
| `Start` | `Space` | In READY state, loads the model and starts inference. In PAUSED state with the same policy path, resumes inference. |
| `Stop` | `Ctrl+Shift+S` | Pauses inference. The model remains in memory. |
| `Clear` | `Esc` | Stops inference and clears the model, session, and action buffer. |
| `Record` | `R` | Starts recording inference results when the `Record` checkbox is enabled. |
| `Save` | `R` | Saves the current inference recording. |
| `Discard` | - | Discards the current inference recording. |

If `Start` is disabled, check the Docker backend status in the right panel. Common causes are `OFF`, `Image missing`, or `Warming up`.

## 9. Model File Locations

Docker Compose bind-mounts host checkpoint directories into each policy container.

| Backend | Host path | Container path |
| --- | --- | --- |
| LeRobot | `/home/robotis/cyclo_intelligence/cyclo_brain/policy/lerobot/checkpoints` | `/policy_checkpoints/lerobot` |
| GR00T | `/home/robotis/cyclo_intelligence/cyclo_brain/policy/groot/checkpoints` | `/policy_checkpoints/groot` |

If the host model is here:

```text
/home/robotis/cyclo_intelligence/cyclo_brain/policy/lerobot/checkpoints/Act_test_20k
```

Enter this path in the UI:

```text
/policy_checkpoints/lerobot/Act_test_20k
```

You can also enter a Hugging Face repo ID directly.

```text
Dongkkka/Act_test_20k
Dongkkka/cyclo_intelligence_groot_n1.7_model
```

To pre-download a model, run `snapshot_download` inside the matching policy container.

LeRobot example:

```bash
docker exec -it lerobot_server bash
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    "Dongkkka/Act_test_20k",
    local_dir="/policy_checkpoints/lerobot/Act_test_20k",
)
PY
```

GR00T N1.7:

```bash
docker exec -it groot_server bash
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    "Dongkkka/cyclo_intelligence_groot_n1.7_model",
    local_dir="/policy_checkpoints/groot/cyclo_intelligence_groot_n1.7_model",
)
PY
```

The Hugging Face cache remains under this host directory in the Compose setup:

```text
/home/robotis/cyclo_intelligence/docker/huggingface
```

## 10. Docker Image Preparation

A local Docker image is required for normal operation.

LeRobot:

```bash
docker pull robotis/lerobot-zenoh:1.0.0-arm64
```

GR00T:

```bash
docker pull robotis/groot-zenoh:1.2.0-arm64
```

During local development, LeRobot may also exist with the temporary tag `robotis/lerobot-zenoh:arm64`; the supervisor accepts it as a local image candidate. GR00T is expected to use the release tag `1.2.0-arm64`.

## 11. Troubleshooting

### Start Is Disabled

Check the backend status in the right panel.

- `Image missing`: pull the Docker image.
- `Stopped` or `Not created`: press `ON`.
- `Warming up`: wait until both `Main` and `Engine` are `Up`.
- `Main Down` or `Engine Down`: press `Restart`.

### Model Load Failed

1. Check that `Policy Path` is correct.
2. If you use a local path, make sure it is a container path such as `/policy_checkpoints/...`.
3. If you use a Hugging Face repo ID, check network access and token permissions.
4. Check that the selected UI policy family matches the checkpoint type.
5. Press `Clear` to reset the session.
6. If it still fails, press Docker backend `Restart`, then press `Start` again.

### Actions Look Wrong or Robot Commands Are Missing

1. Check camera, joint state, and command topic status in Topic Monitor.
2. Check that `Inference Hz` matches the training data FPS.
3. Check that `Control Hz` matches the robot command publish rate.
4. Check that the selected policy's expected camera keys match the current robot config.
5. Press `Clear`, then start again.

### Turn Docker Back On After OFF

`OFF` stops the container but does not delete it. Press `ON` to reuse and start the existing container. If you need a fresh container, delete the old container with CLI Docker/Compose commands and create it again.

## 12. Documentation Image Paths

This document uses the following images, which can be moved into the VitePress site later.

```text
docs/assets/cyclo-brain-inference/01-home-robot-type.png
docs/assets/cyclo-brain-inference/02-inference-act-setup.png
docs/assets/cyclo-brain-inference/03-inference-model-list-coming-soon.png
docs/assets/cyclo-brain-inference/04-inference-groot-instruction.png
docs/assets/cyclo-brain-inference/05-cyclo-brain-architecture.png
docs/assets/cyclo-brain-inference/06-inference-lerobot-smolvla-instruction.png
```
