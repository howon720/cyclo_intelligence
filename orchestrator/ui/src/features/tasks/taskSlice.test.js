import reducer, {
  applyServerTaskInfo,
  receiveServerRecordTaskInfo,
  setCameraRecordingMonitor,
  setInferenceMode,
  setRecordingMonitor,
} from './taskSlice';
import { getRecordTaskInfoKey } from '../../utils/taskInfoSync';

describe('taskSlice inference mode', () => {
  test('defaults inference to simulation mode', () => {
    const state = reducer(undefined, { type: '@@INIT' });

    expect(state.taskInfo.inferenceMode).toBe('simulation');
  });

  test('sets inference mode', () => {
    const state = reducer(undefined, setInferenceMode('robot'));

    expect(state.taskInfo.inferenceMode).toBe('robot');
  });
});

describe('taskSlice recording progress', () => {
  test('resets saved segment progress when applying a server task', () => {
    const initial = reducer(undefined, { type: '@@INIT' });
    const state = {
      ...initial,
      plannedSubTasks: ['old 1', 'old 2'],
      plannedCount: 2,
      slotToServerIdx: [0, 1],
      activeSlotIndex: 1,
      taskInfoSync: {
        ...initial.taskInfoSync,
        serverTaskKey: '2:new',
        serverTaskInfo: {
          taskNum: '2',
          taskName: 'new',
          subtaskInstruction: ['new 1', 'new 2'],
        },
      },
    };

    const next = reducer(state, applyServerTaskInfo());

    expect(next.plannedSubTasks).toEqual(['new 1', 'new 2']);
    expect(next.slotToServerIdx).toEqual([-1, -1]);
    expect(next.activeSlotIndex).toBe(0);
  });
});

describe('taskSlice task info sync', () => {
  test('hydrates the segment plan from server task info after refresh', () => {
    const initial = reducer(undefined, { type: '@@INIT' });

    const next = reducer(
      initial,
      receiveServerRecordTaskInfo({
        ...initial.taskInfo,
        taskNum: '11',
        taskName: 'task',
        taskType: 'record',
        taskInstruction: ['main'],
        subtaskInstruction: ['sub 1', 'sub 2', 'sub 3'],
      })
    );

    expect(next.taskInfo.subtaskInstruction).toEqual(['sub 1', 'sub 2', 'sub 3']);
    expect(next.plannedCount).toBe(3);
    expect(next.plannedSubTasks).toEqual(['sub 1', 'sub 2', 'sub 3']);
    expect(next.slotToServerIdx).toEqual([-1, -1, -1]);
    expect(next.activeSlotIndex).toBe(0);
  });

  test('applies server task settings when identity fields stay the same', () => {
    const initial = reducer(undefined, { type: '@@INIT' });
    const localTaskInfo = {
      ...initial.taskInfo,
      taskNum: '11',
      taskName: 'task',
      taskInstruction: ['main'],
      subtaskInstruction: ['sub 1', 'sub 2'],
      serviceType: 'lerobot',
      inferenceMode: 'simulation',
      controlHz: 100,
      inferenceHz: 15,
    };
    const state = {
      ...initial,
      taskInfo: localTaskInfo,
      taskInfoSync: {
        ...initial.taskInfoSync,
        serverTaskKey: getRecordTaskInfoKey(localTaskInfo),
        editBaseServerTaskKey: getRecordTaskInfoKey(localTaskInfo),
        syncStatus: 'synced',
      },
    };

    const next = reducer(
      state,
      receiveServerRecordTaskInfo({
        ...localTaskInfo,
        serviceType: 'groot',
        inferenceMode: 'robot',
        controlHz: 50,
        inferenceHz: 10,
      })
    );

    expect(next.taskInfo.taskNum).toBe('11');
    expect(next.taskInfo.subtaskInstruction).toEqual(['sub 1', 'sub 2']);
    expect(next.taskInfo.serviceType).toBe('groot');
    expect(next.taskInfo.inferenceMode).toBe('robot');
    expect(next.taskInfo.controlHz).toBe(50);
    expect(next.taskInfo.inferenceHz).toBe(10);
    expect(next.taskInfoSync.syncStatus).toBe('synced');
  });
});

describe('taskSlice recording monitor', () => {
  test('preserves camera monitor rows when rosbag monitor updates', () => {
    const initial = reducer(undefined, { type: '@@INIT' });
    const withCamera = reducer(
      initial,
      setCameraRecordingMonitor([
        {
          name: '/camera/image/compressed',
          cameraName: 'cam_left_head',
          rateHz: 30,
          baselineHz: 30,
          secondsSinceLast: 0,
          status: 0,
          source: 'camera',
        },
      ])
    );

    const next = reducer(
      withCamera,
      setRecordingMonitor({
        topics: [{ name: '/joint_states', rateHz: 100, baselineHz: 100, status: 0 }],
        totalReceived: 10,
        totalWritten: 8,
      })
    );

    expect(next.recordingMonitor.topics).toHaveLength(1);
    expect(next.recordingMonitor.cameraTopics).toHaveLength(1);
    expect(next.recordingMonitor.cameraTopics[0].cameraName).toBe('cam_left_head');
    expect(next.recordingMonitor.totalReceived).toBe(10);
    expect(next.recordingMonitor.totalWritten).toBe(8);
  });

  test('updates camera monitor rows independently', () => {
    const state = reducer(
      undefined,
      setRecordingMonitor({
        topics: [{ name: '/joint_states', rateHz: 100, baselineHz: 100, status: 0 }],
      })
    );

    const next = reducer(
      state,
      setCameraRecordingMonitor([
        {
          name: '/camera/image/compressed',
          cameraName: 'cam_right_wrist',
          rateHz: 0,
          baselineHz: 30,
          secondsSinceLast: 2.5,
          status: 2,
          source: 'camera',
        },
      ])
    );

    expect(next.recordingMonitor.topics).toHaveLength(1);
    expect(next.recordingMonitor.cameraTopics).toHaveLength(1);
    expect(next.recordingMonitor.cameraTopics[0].status).toBe(2);
  });
});
