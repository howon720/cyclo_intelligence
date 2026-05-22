"""Resource-lifecycle tests for the recording-format-v2 VideoRecorder."""

from __future__ import annotations

from queue import Empty, Full, Queue
import shutil
import subprocess
import sys
import threading
import types

import pytest


for mod_name in [
    "rclpy",
    "rclpy.callback_groups",
    "rclpy.node",
    "rclpy.qos",
    "sensor_msgs",
    "sensor_msgs.msg",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)


sys.modules["rclpy.callback_groups"].CallbackGroup = object
sys.modules["rclpy.callback_groups"].ReentrantCallbackGroup = object
sys.modules["rclpy.node"].Node = object
sys.modules["rclpy.qos"].DurabilityPolicy = types.SimpleNamespace(VOLATILE=1)
sys.modules["rclpy.qos"].HistoryPolicy = types.SimpleNamespace(KEEP_LAST=1)
sys.modules["rclpy.qos"].ReliabilityPolicy = types.SimpleNamespace(BEST_EFFORT=1)


class _QoSProfile:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


sys.modules["rclpy.qos"].QoSProfile = _QoSProfile
sys.modules["sensor_msgs.msg"].CompressedImage = object

try:
    import pyarrow  # noqa: F401
    import pyarrow.parquet  # noqa: F401
except ImportError:
    pyarrow_stub = types.ModuleType("pyarrow")
    pyarrow_stub.int32 = lambda: "int32"
    pyarrow_stub.int64 = lambda: "int64"
    pyarrow_stub.array = lambda values, type=None: list(values)
    pyarrow_stub.schema = lambda fields: fields
    pyarrow_stub.table = lambda data, schema=None: {"data": data, "schema": schema}
    sys.modules["pyarrow"] = pyarrow_stub
    parquet_stub = types.ModuleType("pyarrow.parquet")
    parquet_stub.ParquetWriter = object
    sys.modules["pyarrow.parquet"] = parquet_stub


from cyclo_data.recorder.video_recorder import (  # noqa: E402
    _DEFAULT_QUEUE_MAX,
    _CameraStream,
    _JPEG_SENTINEL,
    _JPEG_SOI,
    _resolve_queue_max,
    VideoRecorder,
)


class _Logger:
    def __init__(self, events):
        self.events = events

    def info(self, message):
        self.events.append(("info", message))

    def warn(self, message):
        self.events.append(("warn", message))

    def warning(self, message):
        self.events.append(("warning", message))

    def error(self, message):
        self.events.append(("error", message))


class _Node:
    def __init__(self, events):
        self._logger = _Logger(events)

    def get_logger(self):
        return self._logger


class _FakeStdin:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def write(self, data):
        self.events.append(("stdin.write", len(data)))

    def close(self):
        self.closed = True
        self.events.append(("stdin.close", None))


class _FakeProcess:
    def __init__(self, events):
        self.events = events
        self.stdin = _FakeStdin(events)

    def wait(self, timeout):
        self.events.append(("process.wait", timeout))
        return 0

    def kill(self):
        self.events.append(("process.kill", None))


class _FakeWorker:
    def __init__(self, events):
        self.events = events

    def join(self, timeout):
        self.events.append(("worker.join", timeout))

    def is_alive(self):
        return False


class _FakeWriter:
    def __init__(self, events):
        self.events = events

    def close(self):
        self.events.append(("writer.close", None))


def _recorder_with_stream(stream):
    events = []
    recorder = VideoRecorder.__new__(VideoRecorder)
    recorder._node = _Node(events)
    recorder._recording_active = True
    recorder._streams = {stream.name: stream}
    return recorder, events


def test_queue_max_default_env_override_and_invalid(monkeypatch):
    monkeypatch.delenv("CYCLO_VIDEO_RECORDER_QUEUE_MAX", raising=False)
    assert _resolve_queue_max() == _DEFAULT_QUEUE_MAX == 256

    monkeypatch.setenv("CYCLO_VIDEO_RECORDER_QUEUE_MAX", "7")
    assert _resolve_queue_max() == 7

    monkeypatch.setenv("CYCLO_VIDEO_RECORDER_QUEUE_MAX", "invalid")
    assert _resolve_queue_max() == _DEFAULT_QUEUE_MAX


def test_ffmpeg_trailer_is_non_decodable_soi_marker():
    assert _JPEG_SENTINEL == _JPEG_SOI
    assert len(_JPEG_SENTINEL) == 2


def test_ffmpeg_trailer_flushes_without_extra_gray_frame(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        pytest.skip(f"OpenCV/numpy unavailable: {exc}")

    frames = []
    for value in (32, 96, 224):
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        image[:, :, 0] = value
        image[:, :, 1] = 255 - value
        image[:, :, 2] = (value * 2) % 255
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        frames.append(encoded.tobytes())

    output = tmp_path / "out.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-use_wallclock_as_timestamps",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-i",
        "pipe:0",
        "-c:v",
        "copy",
        "-an",
        "-fps_mode",
        "passthrough",
        "-video_track_timescale",
        "90000",
        str(output),
    ]
    result = subprocess.run(
        command,
        input=b"".join(frames) + _JPEG_SENTINEL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")

    capture = cv2.VideoCapture(str(output))
    decoded = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        decoded.append(frame)
    capture.release()

    assert len(decoded) == len(frames)
    last = decoded[-1]
    assert not np.allclose(last.mean(axis=(0, 1)), [128, 128, 128], atol=2)
    assert last.std() > 0


def test_enqueue_stop_sentinel_retries_when_queue_is_full():
    class FullThenWritableQueue:
        def __init__(self):
            self.items = ["frame"]

        def put(self, item, timeout):
            raise Full

        def get_nowait(self):
            if not self.items:
                raise Empty
            return self.items.pop(0)

        def put_nowait(self, item):
            self.items.append(item)

    stream = _CameraStream(
        name="cam0", topic="/cam0", queue=FullThenWritableQueue()
    )
    recorder, _ = _recorder_with_stream(stream)

    recorder._enqueue_stop_sentinel(stream)

    assert stream.queue.items == [None]


def test_stop_episode_final_join_happens_before_writer_close():
    stream = _CameraStream(name="cam0", topic="/cam0", queue=Queue())
    recorder, events = _recorder_with_stream(stream)
    stream.process = _FakeProcess(events)
    stream.worker = _FakeWorker(events)
    stream.writer = _FakeWriter(events)

    stats = recorder.stop_episode()

    names = [event[0] for event in events]
    assert ("stdin.write", len(_JPEG_SENTINEL)) in events
    assert names.index("stdin.write") < names.index("stdin.close")
    assert names.index("stdin.close") < names.index("process.wait")
    assert events.count(("worker.join", 10.0)) == 1
    assert events.count(("worker.join", 2.0)) == 1
    assert names.index("process.wait") < names.index("writer.close")
    assert names.index("worker.join") < names.index("writer.close")
    assert stats["cam0"]["frames_written"] == 0
    assert stream.writer is None
    assert stream.process is None
    assert stream.worker is None


def test_worker_flush_holds_writer_lock():
    events = []
    recorder = VideoRecorder.__new__(VideoRecorder)
    recorder._node = _Node(events)

    stream = _CameraStream(name="cam0", topic="/cam0", queue=Queue())
    stream.process = types.SimpleNamespace(
        stdin=types.SimpleNamespace(write=lambda data: None)
    )

    class Writer:
        def write_table(self, table):
            events.append(("writer.locked", stream.writer_lock.locked()))

    stream.writer = Writer()
    stream.queue.put((b"\xff\xd8jpeg", 1, 2))
    stream.queue.put(None)

    recorder._worker_loop(stream)

    assert ("writer.locked", True) in events
    assert stream.frames_written == 1
