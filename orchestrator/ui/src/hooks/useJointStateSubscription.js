import { useEffect, useRef, useCallback } from 'react';
import { useSelector } from 'react-redux';
import ROSLIB from 'roslib';

// Single unified joint_states topic — carries all upper-body joints
// (arm_l × 8, arm_r × 8, head × 2, lift × 1 = 19 joints) at 100 Hz.
// Phase 4 yaml's observation.state.upper_body.topic. The legacy
// cyclo_intelligence four-topic split (per-arm-controller) caused each
// joint group to update at only ~7.5 Hz on the URDF (one global throttle
// shared across four 100 Hz streams), which is why the 3D viewer used
// to lag the real robot.
const JOINT_STATE_TOPIC = {
  name: '/joint_states',
  type: 'sensor_msgs/msg/JointState',
};

const ACTION_CHUNK_TOPIC = {
  name: '/inference/trajectory_preview',
  type: 'trajectory_msgs/msg/JointTrajectory',
};

const ACTION_COMMAND_TOPICS = [
  {
    name: '/leader/joint_trajectory_command_broadcaster_left/joint_trajectory',
    type: 'trajectory_msgs/msg/JointTrajectory',
  },
  {
    name: '/leader/joint_trajectory_command_broadcaster_right/joint_trajectory',
    type: 'trajectory_msgs/msg/JointTrajectory',
  },
  {
    name: '/leader/joystick_controller_left/joint_trajectory',
    type: 'trajectory_msgs/msg/JointTrajectory',
  },
  {
    name: '/leader/joystick_controller_right/joint_trajectory',
    type: 'trajectory_msgs/msg/JointTrajectory',
  },
];

const THROTTLE_MS = 33;

export default function useJointStateSubscription(
  setJointValues,
  setActionChunk,
  enabled = true,
  options = {},
) {
  const rosHost = useSelector((state) => state.ros.rosHost);
  const subscribersRef = useRef([]);
  const lastJointUpdateRef = useRef(0);
  const lastActionUpdateRef = useRef(0);
  const visualizationSource = options.visualizationSource || 'state';

  const handleJointState = useCallback(
    (msg) => {
      const now = Date.now();
      if (now - lastJointUpdateRef.current < THROTTLE_MS) return;
      lastJointUpdateRef.current = now;

      if (msg.name && msg.position) {
        setJointValues({ name: msg.name, position: msg.position });
      }
    },
    [setJointValues]
  );

  const handleActionChunk = useCallback(
    (msg) => {
      if (!msg.joint_names || !msg.points || msg.points.length === 0) return;

      if (setActionChunk) {
        setActionChunk({
          names: msg.joint_names,
          points: msg.points.map((p) => p.positions),
        });
      }
    },
    [setActionChunk]
  );

  const handleActionCommand = useCallback(
    (msg) => {
      if (!msg.joint_names || !msg.points || msg.points.length === 0) return;

      const firstPoint = msg.points[0];
      const positions = firstPoint?.positions;
      if (!positions || positions.length === 0) return;

      if (visualizationSource === 'action') {
        const now = Date.now();
        if (now - lastActionUpdateRef.current >= THROTTLE_MS) {
          lastActionUpdateRef.current = now;
          setJointValues({ name: msg.joint_names, position: positions });
        }
      }

      if (setActionChunk) {
        setActionChunk({
          names: msg.joint_names,
          points: msg.points.map((p) => p.positions),
        });
      }
    },
    [setJointValues, setActionChunk, visualizationSource]
  );

  useEffect(() => {
    if (!enabled || !rosHost) return;

    const ros = new ROSLIB.Ros({ url: `ws://${rosHost}:9090` });
    const subs = [];

    ros.on('connection', () => {
      if (visualizationSource === 'state') {
        const jointSub = new ROSLIB.Topic({
          ros,
          name: JOINT_STATE_TOPIC.name,
          messageType: JOINT_STATE_TOPIC.type,
          throttle_rate: THROTTLE_MS,
        });
        jointSub.subscribe(handleJointState);
        subs.push(jointSub);
      }

      const chunkSub = new ROSLIB.Topic({
        ros,
        name: ACTION_CHUNK_TOPIC.name,
        messageType: ACTION_CHUNK_TOPIC.type,
      });
      chunkSub.subscribe(handleActionChunk);
      subs.push(chunkSub);

      ACTION_COMMAND_TOPICS.forEach((topic) => {
        const commandSub = new ROSLIB.Topic({
          ros,
          name: topic.name,
          messageType: topic.type,
          throttle_rate: THROTTLE_MS,
        });
        commandSub.subscribe(handleActionCommand);
        subs.push(commandSub);
      });

      subscribersRef.current = subs;
    });

    ros.on('error', (err) => {
      console.error('Joint state ROS connection error:', err);
    });

    return () => {
      subs.forEach((sub) => {
        try {
          sub.unsubscribe();
        } catch (_e) { /* ignore */ }
      });
      subscribersRef.current = [];
      try {
        ros.close();
      } catch (_e) { /* ignore */ }
    };
  }, [
    enabled,
    rosHost,
    handleJointState,
    handleActionChunk,
    handleActionCommand,
    visualizationSource,
  ]);
}
