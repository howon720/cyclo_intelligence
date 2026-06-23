#!/usr/bin/env python3
#
# Copyright 2026 ROBOTIS CO., LTD.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""CRC-filtered cache for the large Navigation OccupancyGrid topics."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
import zlib


logger = logging.getLogger("supervisor_api.navigation_topics")

GRID_TOPICS = frozenset({"/map", "/global_costmap/costmap"})


def occupancy_grid_data_crc32(message: Any) -> int | None:
    """Return CRC32 of OccupancyGrid.data without constructing a Python list."""
    data = (
        message.get("data")
        if isinstance(message, dict)
        else getattr(message, "data", None)
    )
    if data is None:
        return None
    try:
        return zlib.crc32(data)
    except (BufferError, TypeError, ValueError):
        pass
    try:
        return zlib.crc32(memoryview(data))
    except (BufferError, TypeError, ValueError):
        pass
    if not isinstance(data, list):
        return None
    try:
        marker = 0
        chunk = bytearray()
        for value in data:
            chunk.append(int(value) & 0xFF)
            if len(chunk) == 65536:
                marker = zlib.crc32(chunk, marker)
                chunk.clear()
        return zlib.crc32(chunk, marker)
    except (TypeError, ValueError, OverflowError):
        return None


def _time_to_dict(value: Any) -> dict[str, int]:
    return {
        "sec": int(getattr(value, "sec", 0)),
        "nanosec": int(getattr(value, "nanosec", 0)),
    }


def _pose_to_dict(value: Any) -> dict[str, Any]:
    position = value.position
    orientation = value.orientation
    return {
        "position": {
            "x": float(position.x),
            "y": float(position.y),
            "z": float(position.z),
        },
        "orientation": {
            "x": float(orientation.x),
            "y": float(orientation.y),
            "z": float(orientation.z),
            "w": float(orientation.w),
        },
    }


def occupancy_grid_to_dict(message: Any) -> dict[str, Any]:
    """Convert only the OccupancyGrid fields consumed by the Navigation UI."""
    if isinstance(message, dict):
        return message
    return {
        "header": {
            "stamp": _time_to_dict(message.header.stamp),
            "frame_id": message.header.frame_id,
        },
        "info": {
            "map_load_time": _time_to_dict(message.info.map_load_time),
            "resolution": float(message.info.resolution),
            "width": int(message.info.width),
            "height": int(message.info.height),
            "origin": _pose_to_dict(message.info.origin),
        },
        "data": list(message.data),
    }


class OccupancyGridCache:
    """Keep the newest changed grid and serialize it once per client update."""

    def __init__(self, topic: str) -> None:
        if topic not in GRID_TOPICS:
            raise ValueError(f"Unsupported grid topic: {topic}")
        self.topic = topic
        self._lock = threading.Lock()
        self._crc32: int | None = None
        self._message: Any | None = None

    def cache_ros_message(self, message: Any) -> None:
        marker = occupancy_grid_data_crc32(message)
        if marker is None:
            return
        with self._lock:
            if marker == self._crc32:
                return
            self._crc32 = marker
            self._message = message

    def serialized_if_changed(
        self, last_crc32: int | None
    ) -> tuple[int | None, str | None]:
        """Return a WebSocket payload only when this client's marker changed."""
        with self._lock:
            marker = self._crc32
            message = self._message
        if marker is None or message is None or marker == last_crc32:
            return last_crc32, None
        payload = {
            "available": True,
            "data": occupancy_grid_to_dict(message),
        }
        return marker, json.dumps(payload, separators=(",", ":"))


GRID_CACHES = {topic: OccupancyGridCache(topic) for topic in GRID_TOPICS}
_ros_start_lock = threading.Lock()
_ros_thread: threading.Thread | None = None


def _ros_grid_spin() -> None:
    import rclpy
    from nav_msgs.msg import OccupancyGrid
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    try:
        if not rclpy.ok():
            rclpy.init()
        node = Node("cyclo_navigation_grid_cache")
        fallback_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        discovered_qos = {}
        for _ in range(20):
            for topic in GRID_TOPICS:
                publishers = node.get_publishers_info_by_topic(topic)
                if publishers:
                    discovered_qos[topic] = publishers[0].qos_profile
            if len(discovered_qos) == len(GRID_TOPICS):
                break
            executor.spin_once(timeout_sec=0.1)
        subscriptions = [
            node.create_subscription(
                OccupancyGrid,
                topic,
                GRID_CACHES[topic].cache_ros_message,
                discovered_qos.get(topic, fallback_qos),
            )
            for topic in GRID_TOPICS
        ]
        node._navigation_grid_subscriptions = subscriptions
        executor.spin()
    except Exception:
        logger.exception("Navigation ROS2 grid cache stopped")


def ensure_ros_grid_subscriber_started() -> None:
    """Start the single ROS subscriber shared by all WebSocket clients."""
    global _ros_thread
    with _ros_start_lock:
        if _ros_thread is not None and _ros_thread.is_alive():
            return
        _ros_thread = threading.Thread(
            target=_ros_grid_spin,
            daemon=True,
            name="navigation-grid-cache",
        )
        _ros_thread.start()
