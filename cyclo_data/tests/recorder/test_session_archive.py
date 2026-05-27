from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import threading

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "cyclo_data"))
sys.path.insert(0, str(_REPO_ROOT / "orchestrator"))
import cyclo_data  # noqa: E402
import cyclo_data.converter  # noqa: E402
import cyclo_data.hub  # noqa: E402


def _stub_module(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    parts = name.split(".")
    for idx in range(1, len(parts)):
        parent = ".".join(parts[:idx])
        sys.modules.setdefault(parent, ModuleType(parent))
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass


_stub_module("huggingface_hub", HfApi=_Dummy)
_stub_module("interfaces.msg", RecordingStatus=_Dummy)
_stub_module("cyclo_data.converter.orchestrator", DataConverter=_Dummy)
_stub_module(
    "cyclo_data.hub.progress_tracker",
    HuggingFaceLogCapture=_Dummy,
    HuggingFaceProgressTqdm=_Dummy,
)

from cyclo_data.recorder.session_manager import DataManager  # noqa: E402


def _make_manager(root: Path, *, subtask_total: int = 2) -> DataManager:
    manager = DataManager.__new__(DataManager)
    manager._save_rosbag_path = str(root)
    manager._segmented_storage_mode = True
    manager._physical_segment_total = subtask_total
    manager._subtask_mode = subtask_total > 1
    manager._main_task_instruction = "main instruction"
    manager._task_info = SimpleNamespace(task_num="1234", task_name="archive test")
    manager._robot_type = "test_robot"
    manager._state_lock = threading.Lock()
    return manager


def _write_segment(
    root: Path,
    *,
    full_idx: int,
    subtask_idx: int,
    subtask_total: int,
    with_video: bool = True,
) -> Path:
    segment = root / str(full_idx) / "segments" / str(subtask_idx)
    segment.mkdir(parents=True, exist_ok=True)
    (segment / f"segment_{subtask_idx}.mcap").write_bytes(
        f"mcap-{subtask_idx}".encode()
    )
    start_ns = 1_000_000_000 + subtask_idx * 100_000_000
    duration_ns = 50_000_000
    metadata = {
        "rosbag2_bagfile_information": {
            "version": 9,
            "storage_identifier": "mcap",
            "duration": {"nanoseconds": duration_ns},
            "starting_time": {"nanoseconds_since_epoch": start_ns},
            "message_count": 3,
            "topics_with_message_count": [
                {
                    "topic_metadata": {
                        "name": "/joint_states",
                        "type": "sensor_msgs/msg/JointState",
                        "serialization_format": "cdr",
                        "offered_qos_profiles": "",
                    },
                    "message_count": 3,
                }
            ],
            "compression_format": "",
            "compression_mode": "",
            "relative_file_paths": [f"segment_{subtask_idx}.mcap"],
            "files": [
                {
                    "path": f"segment_{subtask_idx}.mcap",
                    "starting_time": {"nanoseconds_since_epoch": start_ns},
                    "duration": {"nanoseconds": duration_ns},
                    "message_count": 3,
                }
            ],
            "custom_data": None,
            "ros_distro": "jazzy",
        }
    }
    (segment / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False),
        encoding="utf-8",
    )
    info = {
        "recording_mode": "subtask" if subtask_total > 1 else "single_segment",
        "full_episode_index": full_idx,
        "subtask_index": subtask_idx,
        "subtask_total": subtask_total,
        "episode_index": subtask_idx,
        "subtask_instruction": f"subtask {subtask_idx}",
    }
    if with_video:
        videos = segment / "videos"
        videos.mkdir()
        (videos / "cam0.mp4").write_bytes(b"raw-mjpeg")
        (videos / "cam0_timestamps.parquet").write_bytes(b"timestamps")
        info["video_stats"] = {"cam0": {"frames_written": 1}}
    (segment / "episode_info.json").write_text(json.dumps(info, indent=2))
    return segment


def test_archive_moves_segmented_files_and_marks_pending(tmp_path):
    root = tmp_path / "Task_1234_archive_MCAP"
    manager = _make_manager(root, subtask_total=2)
    first = _write_segment(root, full_idx=0, subtask_idx=0, subtask_total=2)
    second = _write_segment(root, full_idx=0, subtask_idx=1, subtask_total=2)

    out = manager._archive_full_episode(0)

    assert out == root / "0"
    assert (out / "0_0.mcap").read_bytes() == b"mcap-0"
    assert (out / "0_1.mcap").read_bytes() == b"mcap-1"
    assert not (first / "segment_0.mcap").exists()
    assert not (second / "segment_1.mcap").exists()
    assert not (out / "segments").exists()
    assert (out / "videos" / "0_0" / "cam0.mp4").read_bytes() == b"raw-mjpeg"
    assert (out / "videos" / "0_0" / "cam0_timestamps.parquet").read_bytes() == (
        b"timestamps"
    )

    info = json.loads((out / "episode_info.json").read_text())
    assert info["transcoding_status"] == "pending"


def test_archive_marks_episode_without_videos_not_required(tmp_path):
    root = tmp_path / "Task_1234_archive_MCAP"
    manager = _make_manager(root, subtask_total=1)
    _write_segment(
        root,
        full_idx=0,
        subtask_idx=0,
        subtask_total=1,
        with_video=False,
    )

    out = manager._archive_full_episode(0)

    assert out == root / "0"
    assert not (out / "segments").exists()
    info = json.loads((out / "episode_info.json").read_text())
    assert info["transcoding_status"] == "not_required"
