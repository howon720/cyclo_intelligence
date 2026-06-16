import reducer, { applyServerTaskInfo, setInferenceMode } from './taskSlice';

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
