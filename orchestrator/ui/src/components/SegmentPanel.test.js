import { configureStore } from '@reduxjs/toolkit';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import toast from 'react-hot-toast';
import SegmentPanel from './SegmentPanel';
import taskReducer from '../features/tasks/taskSlice';
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

const renderPanel = () => {
  const sendRecordCommand = jest.fn().mockResolvedValue({
    success: true,
    message: 'ok',
  });
  useRosServiceCaller.mockReturnValue({ sendRecordCommand });

  const initialTasks = taskReducer(undefined, { type: '@@INIT' });
  const store = configureStore({
    reducer: {
      tasks: taskReducer,
    },
    preloadedState: {
      tasks: {
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
      },
    },
  });

  render(
    <Provider store={store}>
      <SegmentPanel />
    </Provider>
  );

  return { sendRecordCommand, store };
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

});
