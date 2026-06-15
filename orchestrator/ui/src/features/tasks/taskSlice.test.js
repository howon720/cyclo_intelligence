import reducer, { setInferenceMode } from './taskSlice';

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
