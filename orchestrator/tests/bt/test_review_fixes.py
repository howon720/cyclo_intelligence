#!/usr/bin/env python3
#
# Copyright 2026 ROBOTIS CO., LTD.
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

import sys
import types


class _QoSProfile:
    def __init__(self, *args, **kwargs):
        pass


class _ReliabilityPolicy:
    RELIABLE = object()


def _install_ros_stubs():
    rclpy_mod = types.ModuleType('rclpy')
    qos_mod = types.ModuleType('rclpy.qos')
    qos_mod.QoSProfile = _QoSProfile
    qos_mod.ReliabilityPolicy = _ReliabilityPolicy

    sensor_msgs_mod = types.ModuleType('sensor_msgs')
    sensor_msgs_msg_mod = types.ModuleType('sensor_msgs.msg')
    sensor_msgs_msg_mod.JointState = object

    trajectory_msgs_mod = types.ModuleType('trajectory_msgs')
    trajectory_msgs_msg_mod = types.ModuleType('trajectory_msgs.msg')
    trajectory_msgs_msg_mod.JointTrajectory = object
    trajectory_msgs_msg_mod.JointTrajectoryPoint = object

    sys.modules.setdefault('rclpy', rclpy_mod)
    sys.modules.setdefault('rclpy.qos', qos_mod)
    sys.modules.setdefault('sensor_msgs', sensor_msgs_mod)
    sys.modules.setdefault('sensor_msgs.msg', sensor_msgs_msg_mod)
    sys.modules.setdefault('trajectory_msgs', trajectory_msgs_mod)
    sys.modules.setdefault('trajectory_msgs.msg', trajectory_msgs_msg_mod)


_install_ros_stubs()

from orchestrator.bt.actions.joint_control import _coerce_positions  # noqa: E402
from orchestrator.bt.node_registry import _annotation_to_port_type  # noqa: E402


def test_coerce_positions_accepts_comma_separated_strings():
    assert _coerce_positions('0.0, 1.5, -2') == [0.0, 1.5, -2.0]
    assert _coerce_positions('') == []


def test_stringified_annotations_map_to_port_types():
    assert _annotation_to_port_type('bool', None) == 'bool'
    assert _annotation_to_port_type('list[float]', None) == 'number'
    assert _annotation_to_port_type('Optional[int]', None) == 'number'
    assert _annotation_to_port_type('str', None) == 'string'
