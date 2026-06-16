import { configureStore } from '@reduxjs/toolkit';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import toast from 'react-hot-toast';
import SegmentPanel from './SegmentPanel';
import taskReducer, { setRecordStatus } from '../features/tasks/taskSlice';
import { RecordPhase } from '../constants/taskPhases';
import { useRosServiceCaller } from '../hooks/useRosServiceCaller';

jest.mock('react-hot-toast', () => {
  const toast = jest.fn();
  toast.error = jest.fn();
  toast.success = jest.fn();
  return {
    __esModule: true,
    default: toast,
  };
});

jest.mock('../hooks/useRosServiceCaller', () => ({
  useRosServiceCaller: jest.fn(),
}));

jest.mock('./InfoPanel', () => function MockInfoPanel() {
  return <div data-testid="info-panel" />;
});

const renderPanel = ({ taskOverrides = {}, sendRecordCommand } = {}) => {
  const sendCommand = sendRecordCommand || jest.fn().mockResolvedValue({
    success: true,
    message: 'ok',
  });
  useRosServiceCaller.mockReturnValue({ sendRecordCommand: sendCommand });

  const initialTasks = taskReducer(undefined, { type: '@@INIT' });
  const tasks = {
    ...initialTasks,
    taskInfo: {
      ...initialTasks.taskInfo,
      taskNum: '1',
      taskName: 'discard-target-test',
    },
    recordStatus: {
      ...initialTasks.recordStatus,
      recordPhase: RecordPhase.READY,
      currentEpisodeNumber: 7,
      currentSubtaskIndex: 1,
      subtaskCount: 2,
      topicReceived: true,
    },
    plannedCount: 2,
    plannedSubTasks: ['pick', 'place'],
    slotToServerIdx: [0, -1],
    activeSlotIndex: 1,
    ...taskOverrides,
  };
  const store = configureStore({
    reducer: {
      tasks: taskReducer,
    },
    preloadedState: {
      tasks,
    },
  });

  render(
    <Provider store={store}>
      <SegmentPanel />
    </Provider>
  );

  return { sendRecordCommand: sendCommand, store };
};

describe('SegmentPanel discard episode target', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('sends the captured full episode index when discarding an episode', async () => {
    const { sendRecordCommand } = renderPanel();

    const discardButton = await screen.findByRole('button', {
      name: /discard episode/i,
    });
    await waitFor(() => expect(discardButton).toBeEnabled());

    fireEvent.click(discardButton);

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledWith(
        'discard_episode',
        expect.objectContaining({
          segmentIndex: 8,
          subtaskInstruction: ['pick', 'place'],
        })
      );
    });
    expect(toast.success).toHaveBeenCalledWith('Discard episode: ok');
  });

  test('drops duplicate save clicks while a save sequence is in flight', async () => {
    const sendRecordCommand = jest.fn(() => new Promise(() => {}));
    renderPanel({
      sendRecordCommand,
      taskOverrides: {
        recordStatus: {
          recordPhase: RecordPhase.RECORDING,
          running: true,
          currentEpisodeNumber: 7,
          currentSubtaskIndex: 0,
          subtaskCount: 2,
          topicReceived: true,
        },
        slotToServerIdx: [-1, -1],
        activeSlotIndex: 0,
      },
    });

    const saveButton = await screen.findByRole('button', {
      name: /save subtask 1/i,
    });

    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledTimes(1);
    });
    expect(sendRecordCommand).toHaveBeenCalledWith(
      'stop_segment',
      expect.objectContaining({
        segmentIndex: 0,
      })
    );
  });

  test('does not restore saved checkmarks from stale status after discarding episode', async () => {
    const { sendRecordCommand, store } = renderPanel({
      taskOverrides: {
        recordStatus: {
          recordPhase: RecordPhase.RECORDING,
          running: true,
          currentEpisodeNumber: 0,
          currentSubtaskIndex: 2,
          subtaskCount: 3,
          topicReceived: true,
        },
        plannedCount: 3,
        plannedSubTasks: ['pick', 'place', 'release'],
        slotToServerIdx: [0, 1, -1],
        activeSlotIndex: 2,
      },
    });

    const discardButton = await screen.findByRole('button', {
      name: /discard episode/i,
    });
    await waitFor(() => expect(discardButton).toBeEnabled());

    fireEvent.click(discardButton);

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledWith(
        'discard_episode',
        expect.objectContaining({ segmentIndex: 1 })
      );
      expect(store.getState().tasks.slotToServerIdx).toEqual([-1, -1, -1]);
    });

    await act(async () => {
      store.dispatch(setRecordStatus({
        recordPhase: RecordPhase.READY,
        running: false,
        currentEpisodeNumber: 0,
        currentSubtaskIndex: 2,
        subtaskCount: 3,
        savedSubtaskIndices: [],
        topicReceived: true,
      }));
      await Promise.resolve();
    });

    expect(store.getState().tasks.slotToServerIdx).toEqual([-1, -1, -1]);
    expect(store.getState().tasks.activeSlotIndex).toBe(0);
  });

  test('uses saved subtask indices as authoritative status instead of cursor inference', async () => {
    const { store } = renderPanel({
      taskOverrides: {
        recordStatus: {
          recordPhase: RecordPhase.READY,
          running: false,
          currentEpisodeNumber: 0,
          currentSubtaskIndex: 2,
          subtaskCount: 3,
          savedSubtaskIndices: [0, 2],
          topicReceived: true,
        },
        plannedCount: 3,
        plannedSubTasks: ['pick', 'place', 'release'],
        slotToServerIdx: [0, 1, 2],
        activeSlotIndex: 2,
      },
    });

    await waitFor(() => {
      expect(store.getState().tasks.slotToServerIdx).toEqual([0, -1, 2]);
      expect(store.getState().tasks.activeSlotIndex).toBe(1);
    });
  });

  test('finishes episode after re-recording a middle missing subtask', async () => {
    const sendRecordCommand = jest.fn().mockResolvedValue({
      success: true,
      message: 'ok',
    });
    renderPanel({
      sendRecordCommand,
      taskOverrides: {
        recordStatus: {
          recordPhase: RecordPhase.RECORDING,
          running: true,
          currentEpisodeNumber: 0,
          currentSubtaskIndex: 1,
          subtaskCount: 3,
          savedSubtaskIndices: [0, 2],
          topicReceived: true,
        },
        plannedCount: 3,
        plannedSubTasks: ['pick', 'place', 'release'],
        slotToServerIdx: [0, -1, 2],
        activeSlotIndex: 1,
      },
    });

    const saveButton = await screen.findByRole('button', {
      name: /save subtask 2/i,
    });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledWith(
        'stop_segment',
        expect.objectContaining({ segmentIndex: 1 })
      );
      expect(sendRecordCommand).toHaveBeenCalledWith(
        'finish_episode',
        expect.objectContaining({
          subtaskInstruction: ['pick', 'place', 'release'],
        })
      );
    });
    expect(sendRecordCommand).not.toHaveBeenCalledWith(
      'start_segment',
      expect.anything()
    );
  });

});
