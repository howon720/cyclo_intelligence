from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "cyclo_data"))
sys.path.insert(0, str(_REPO_ROOT / "orchestrator"))
sys.path.insert(0, str(_REPO_ROOT / "shared"))

import cyclo_data  # noqa: E402
import cyclo_data.recorder  # noqa: E402
import cyclo_data.services  # noqa: E402


def _stub_module(name: str, **attrs) -> None:
    if name in sys.modules:
        module = sys.modules[name]
        for key, value in attrs.items():
            setattr(module, key, value)
        return
    parts = name.split(".")
    for idx in range(1, len(parts)):
        parent = ".".join(parts[:idx])
        sys.modules.setdefault(parent, ModuleType(parent))
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _RecordingStatus:
    READY = 0
    RECORDING = 1
    SAVING = 2


class _DataOperationStatus:
    IDLE = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    CANCELLED = 4


class _RecordingCommand:
    class Request:
        START = 0
        STOP = 1
        PAUSE = 2
        RESUME = 3
        FINISH = 4
        MOVE_TO_NEXT = 5
        RERECORD = 6
        SKIP_TASK = 7
        CANCEL = 8
        REFRESH_TOPICS = 9
        START_SEGMENT = 10
        STOP_SEGMENT = 11
        DISCARD_SEGMENT = 12
        FINISH_EPISODE = 13
        DISCARD_EPISODE = 14
        SET_TASK_INFO = 15
        CANCEL_SEGMENT = 16


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass


_stub_module(
    "interfaces.msg",
    DataOperationStatus=_DataOperationStatus,
    RecordingStatus=_RecordingStatus,
)
_stub_module("interfaces.srv", RecordingCommand=_RecordingCommand)
_stub_module("cyclo_data.recorder.camera_info_snapshot", CameraInfoSnapshot=_Dummy)
_stub_module("cyclo_data.recorder.rosbag_control", RosbagControl=_Dummy)
_stub_module("cyclo_data.recorder.transcoder", TranscodeWorker=_Dummy)
_stub_module("cyclo_data.recorder.video_recorder", VideoRecorder=_Dummy)
_stub_module("huggingface_hub", HfApi=_Dummy)
_stub_module("cyclo_data.converter.orchestrator", DataConverter=_Dummy)
_stub_module(
    "cyclo_data.hub.progress_tracker",
    HuggingFaceLogCapture=_Dummy,
    HuggingFaceProgressTqdm=_Dummy,
)
_stub_module("psutil", cpu_percent=lambda interval=None: 0.0)

from cyclo_data.services.recording_service import RecordingService  # noqa: E402

for _module_name in (
    "cyclo_data.recorder.camera_info_snapshot",
    "cyclo_data.recorder.rosbag_control",
    "cyclo_data.recorder.transcoder",
    "cyclo_data.recorder.video_recorder",
):
    sys.modules.pop(_module_name, None)
    _parent_name, _attr_name = _module_name.rsplit(".", 1)
    _parent = sys.modules.get(_parent_name)
    if _parent is not None and hasattr(_parent, _attr_name):
        delattr(_parent, _attr_name)


def _request(segment_index=0, tags=None, **attrs):
    return SimpleNamespace(
        segment_index=segment_index,
        task_info=SimpleNamespace(tags=tags or []),
        **attrs,
    )


def test_discard_episode_segment_index_zero_keeps_legacy_cursor_behavior():
    assert RecordingService._extract_full_episode_index(_request(0)) is None


def test_discard_episode_segment_index_encodes_full_episode_index_plus_one():
    assert RecordingService._extract_full_episode_index(_request(1)) == 0
    assert RecordingService._extract_full_episode_index(_request(8)) == 7


def test_discard_episode_accepts_transitional_explicit_target_fields():
    req = _request(0, has_full_episode_index=True, full_episode_index=7)
    assert RecordingService._extract_full_episode_index(req) == 7


def test_discard_episode_accepts_transitional_target_tag():
    req = _request(0, tags=["recording_full_episode_index:7"])
    assert RecordingService._extract_full_episode_index(req) == 7


class _Logger:
    def __init__(self):
        self.warnings = []
        self.errors = []

    def warn(self, message):
        self.warnings.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass

    def error(self, message):
        self.errors.append(message)


def _service_with_logger():
    logger = _Logger()
    service = RecordingService.__new__(RecordingService)
    service._node = SimpleNamespace(get_logger=lambda: logger)
    return service, logger


def test_validate_active_segment_rejects_stale_segment_request():
    service, logger = _service_with_logger()
    service._data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        get_current_subtask_index=lambda: 1,
    )
    response = SimpleNamespace(success=True, message="")

    ok = service._validate_active_segment(
        _request(segment_index=2),
        response,
        "STOP_SEGMENT",
    )

    assert ok is False
    assert response.success is False
    assert response.message == "STOP_SEGMENT: active subtask is 1, but request targeted 2"
    assert logger.warnings == [response.message]


def test_validate_active_segment_accepts_current_segment_request():
    service, _ = _service_with_logger()
    service._data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        get_current_subtask_index=lambda: 1,
    )
    response = SimpleNamespace(success=True, message="")

    ok = service._validate_active_segment(
        _request(segment_index=1),
        response,
        "STOP_SEGMENT",
    )

    assert ok is True
    assert response.success is True


def test_start_segment_rejects_when_recording_is_already_active():
    service, logger = _service_with_logger()
    service._finish_episode_in_progress = lambda: False
    service._rosbag = SimpleNamespace(is_available=lambda: True)
    data_manager = SimpleNamespace(
        is_recording=lambda: True,
        set_current_subtask_index=lambda index: (_ for _ in ()).throw(
            AssertionError("must not change subtask while recording")
        ),
    )
    service._ensure_data_manager = lambda task_info, robot_type: data_manager
    response = SimpleNamespace(success=True, message="")
    request = _request(
        segment_index=2,
        command=_RecordingCommand.Request.START_SEGMENT,
        robot_type="ffw_sg2_rev1",
    )

    result = service._do_start(request, response)

    assert result is response
    assert response.success is False
    assert response.message == "START blocked: recording already active"
    assert logger.warnings == [response.message]


def test_start_segment_rejects_request_that_skips_next_missing_subtask():
    service, logger = _service_with_logger()
    service._finish_episode_in_progress = lambda: False
    service._rosbag = SimpleNamespace(is_available=lambda: True)
    data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        is_recording=lambda: False,
        missing_subtasks_for_full_episode=lambda: [1, 2],
        set_current_subtask_index=lambda index: (_ for _ in ()).throw(
            AssertionError("must not jump over a missing subtask")
        ),
    )
    service._ensure_data_manager = lambda task_info, robot_type: data_manager
    response = SimpleNamespace(success=True, message="")
    request = _request(
        segment_index=2,
        command=_RecordingCommand.Request.START_SEGMENT,
        robot_type="ffw_sg2_rev1",
    )

    result = service._do_start(request, response)

    assert result is response
    assert response.success is False
    assert response.message == (
        "START_SEGMENT: next available subtask is 1, but request targeted 2"
    )
    assert logger.warnings == [response.message]


def test_start_segment_rejects_when_current_episode_is_already_complete():
    service, logger = _service_with_logger()
    service._finish_episode_in_progress = lambda: False
    service._rosbag = SimpleNamespace(is_available=lambda: True)
    data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        is_recording=lambda: False,
        missing_subtasks_for_full_episode=lambda: [],
        set_current_subtask_index=lambda index: (_ for _ in ()).throw(
            AssertionError("must not restart a complete episode")
        ),
    )
    service._ensure_data_manager = lambda task_info, robot_type: data_manager
    response = SimpleNamespace(success=True, message="")
    request = _request(
        segment_index=1,
        command=_RecordingCommand.Request.START_SEGMENT,
        robot_type="ffw_sg2_rev1",
    )

    result = service._do_start(request, response)

    assert result is response
    assert response.success is False
    assert response.message == (
        "START_SEGMENT: current episode already has all subtasks; "
        "finish or discard episode before starting again"
    )
    assert logger.warnings == [response.message]


def test_finish_episode_rejects_missing_subtasks_before_archive_thread():
    service, logger = _service_with_logger()
    service._data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        is_recording=lambda: False,
        missing_subtasks_for_full_episode=lambda: [1],
    )
    service._start_finish_episode_thread = lambda data_manager: (_ for _ in ()).throw(
        AssertionError("archive thread must not start with missing subtasks")
    )
    response = SimpleNamespace(success=True, message="")
    request = _request(command=_RecordingCommand.Request.FINISH_EPISODE)

    result = service._do_finish_episode(request, response)

    assert result is response
    assert response.success is False
    assert response.message == "FINISH_EPISODE: missing subtask(s) [1]"
    assert logger.warnings == [response.message]


def test_stop_segment_rejects_when_no_active_recording():
    service, _ = _service_with_logger()
    service._data_manager = SimpleNamespace(is_recording=lambda: False)
    response = SimpleNamespace(success=True, message="")
    request = _request(command=_RecordingCommand.Request.STOP_SEGMENT)

    result = service._do_stop_and_save(
        request,
        response,
        "STOP_SEGMENT",
        event="finish",
    )

    assert result is response
    assert response.success is False
    assert response.message == "STOP_SEGMENT: no active recording"


def test_stop_segment_saves_metadata_even_without_urdf_path(tmp_path):
    service, _ = _service_with_logger()
    episode_dir = tmp_path / "0" / "segments" / "0"
    metadata_calls = []
    stopped = []
    events = []

    data_manager = SimpleNamespace(
        _record_episode_count=0,
        _segmented_storage_mode=True,
        is_recording=lambda: True,
        get_current_subtask_index=lambda: 0,
        get_status=lambda: "recording",
        get_save_rosbag_path=lambda: str(episode_dir),
        save_robotis_metadata=lambda **kwargs: metadata_calls.append(kwargs),
        stop_recording=lambda **kwargs: stopped.append(kwargs),
    )
    service._data_manager = data_manager
    service._rosbag = SimpleNamespace(
        stop_rosbag=lambda: None,
        publish_action_event=lambda event: events.append(event),
    )
    service._video_recorder = None
    service._camera_info = None
    service._last_camera_rotations = {"cam0": 0}
    service._last_image_topics = {"cam0": "/image"}
    service._last_camera_info_topics = {"cam0": "/camera_info"}
    service._publish_umbrella_status = lambda *args, **kwargs: None
    response = SimpleNamespace(success=True, message="")
    request = _request(
        command=_RecordingCommand.Request.STOP_SEGMENT,
        segment_index=0,
        urdf_path="",
    )

    result = service._do_stop_and_save(
        request,
        response,
        "STOP_SEGMENT",
        event="finish",
    )

    assert result is response
    assert response.success is True
    assert response.message == "Subtask saved"
    assert len(metadata_calls) == 1
    assert metadata_calls[0]["urdf_path"] == ""
    assert metadata_calls[0]["camera_rotations"] == {"cam0": 0}
    assert stopped == [{"finish_full_episode": False}]
    assert events == ["finish"]


def test_start_segment_rolls_back_rosbag_when_writer_start_fails(tmp_path):
    service, logger = _service_with_logger()
    episode_dir = tmp_path / "0" / "segments" / "0"
    rosbag_calls = []
    stopped_writers = []
    started = []

    class FailingVideoRecorder:
        def start_episode(self, _episode_dir):
            raise RuntimeError("camera writer boom")

        def stop_episode(self):
            stopped_writers.append(True)
            return {}

    data_manager = SimpleNamespace(
        _segmented_storage_mode=True,
        is_recording=lambda: False,
        missing_subtasks_for_full_episode=lambda: [0],
        set_current_subtask_index=lambda index: None,
        get_save_rosbag_path=lambda allow_idle=False: str(episode_dir),
        start_recording=lambda: started.append(True),
    )
    service._finish_episode_in_progress = lambda: False
    service._ensure_data_manager = lambda task_info, robot_type: data_manager
    service._ensure_video_pipeline = lambda robot_type: None
    service._last_prepared_topics = ()
    service._video_recorder = FailingVideoRecorder()
    service._camera_info = None
    service._publish_umbrella_status = lambda *args, **kwargs: None

    def start_rosbag(rosbag_uri):
        rosbag_calls.append(("start", rosbag_uri))
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "partial.mcap").write_bytes(b"partial")

    service._rosbag = SimpleNamespace(
        is_available=lambda: True,
        start_rosbag=start_rosbag,
        stop_and_delete_rosbag=lambda: rosbag_calls.append(("stop_delete", None)),
    )
    response = SimpleNamespace(success=True, message="")
    request = _request(
        command=_RecordingCommand.Request.START_SEGMENT,
        segment_index=0,
        robot_type="ffw_sg2_rev1",
        topics=[],
    )

    result = service._do_start(request, response)

    assert result is response
    assert response.success is False
    assert "camera writer boom" in response.message
    assert rosbag_calls == [
        ("start", str(episode_dir)),
        ("stop_delete", None),
    ]
    assert stopped_writers == [True]
    assert started == []
    assert not episode_dir.exists()
    assert logger.errors == [response.message]


def test_cancel_segment_rejects_when_no_active_recording():
    service, _ = _service_with_logger()
    service._data_manager = SimpleNamespace(is_recording=lambda: False)
    service._publish_umbrella_status = lambda *args, **kwargs: None
    response = SimpleNamespace(success=True, message="")
    request = _request(command=_RecordingCommand.Request.CANCEL_SEGMENT)

    result = service._do_cancel(request, response)

    assert result is response
    assert response.success is False
    assert response.message == "CANCEL_SEGMENT: no active recording"
