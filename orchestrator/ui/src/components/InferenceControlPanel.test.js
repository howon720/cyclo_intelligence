import { configureStore } from '@reduxjs/toolkit';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import toast from 'react-hot-toast';
import InferenceControlPanel from './InferenceControlPanel';
import taskReducer, { setInferenceStatus } from '../features/tasks/taskSlice';
import rosReducer from '../features/ros/rosSlice';
import { InferencePhase } from '../constants/taskPhases';
import { useRosServiceCaller } from '../hooks/useRosServiceCaller';

jest.mock('react-hot-toast', () => {
  const toast = jest.fn();
  toast.error = jest.fn();
  toast.success = jest.fn();
  toast.dismiss = jest.fn();
  return {
    __esModule: true,
    default: toast,
    useToasterStore: () => ({ toasts: [] }),
  };
});

jest.mock('../hooks/useRosServiceCaller', () => ({
  useRosServiceCaller: jest.fn(),
}));

jest.mock('../hooks/usePolicyBackendStatus', () => ({
  __esModule: true,
  default: () => ({
    readiness: {
      ready: true,
      state: 'ready',
      message: 'Backend ready',
    },
    refreshStatus: jest.fn(),
  }),
  getPolicyBackendReadiness: (status) => status,
}));

const renderPanel = ({ inferenceMode = 'robot', sendRecordCommand: sendOverride = null } = {}) => {
  const sendRecordCommand = sendOverride || jest.fn().mockResolvedValue({
    success: true,
    message: 'ok',
  });
  useRosServiceCaller.mockReturnValue({ sendRecordCommand });

  const initialTasks = taskReducer(undefined, { type: '@@INIT' });
  const initialRos = rosReducer(undefined, { type: '@@INIT' });
  const store = configureStore({
    reducer: {
      tasks: taskReducer,
      ros: rosReducer,
    },
    preloadedState: {
      tasks: {
        ...initialTasks,
        taskInfo: {
          ...initialTasks.taskInfo,
          policyPath: '/policy_checkpoints/lerobot/model',
          inferenceMode,
        },
        inferenceStatus: {
          ...initialTasks.inferenceStatus,
          inferencePhase: InferencePhase.READY,
        },
      },
      ros: {
        ...initialRos,
        rosHost: 'localhost',
      },
    },
  });

  render(
    <Provider store={store}>
      <InferenceControlPanel />
    </Provider>
  );

  return { store, sendRecordCommand };
};

describe('InferenceControlPanel deploy safety', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('shows a warning instead of starting immediately for Real Robot Deploy', async () => {
    const { sendRecordCommand } = renderPanel({ inferenceMode: 'robot' });

    fireEvent.click(screen.getByRole('button', { name: /start inference/i }));

    expect(await screen.findByRole('dialog', { name: /real robot deploy/i }))
      .toBeInTheDocument();
    expect(sendRecordCommand).not.toHaveBeenCalled();
  });

  test('starts robot deploy only after explicit confirmation', async () => {
    const { sendRecordCommand } = renderPanel({ inferenceMode: 'robot' });

    fireEvent.click(screen.getByRole('button', { name: /start inference/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^Real Robot Deploy$/i }));

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledWith('start_inference', {
        inferenceMode: 'robot',
      });
    });
  });

  test('can switch the pending start to 3D Sim Deploy from the warning', async () => {
    const { store, sendRecordCommand } = renderPanel({ inferenceMode: 'robot' });

    fireEvent.click(screen.getByRole('button', { name: /start inference/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^3D Sim Deploy$/i }));

    await waitFor(() => {
      expect(store.getState().tasks.taskInfo.inferenceMode).toBe('simulation');
      expect(sendRecordCommand).toHaveBeenCalledWith('start_inference', {
        inferenceMode: 'simulation',
      });
    });
  });

  test('keeps loading state when start command times out after LOADING begins', async () => {
    let rejectStart;
    const sendRecordCommand = jest.fn(() => new Promise((_, reject) => {
      rejectStart = reject;
    }));
    const { store } = renderPanel({
      inferenceMode: 'simulation',
      sendRecordCommand,
    });

    fireEvent.click(screen.getByRole('button', { name: /start inference/i }));

    await waitFor(() => {
      expect(sendRecordCommand).toHaveBeenCalledWith('start_inference', {
        inferenceMode: 'simulation',
      });
    });

    act(() => {
      store.dispatch(setInferenceStatus({ inferencePhase: InferencePhase.LOADING }));
      rejectStart(new Error('Service call timeout for /task/command'));
    });

    await waitFor(() => {
      expect(toast).toHaveBeenCalledWith(
        'Model loading is still running. Large downloads can take several minutes.'
      );
    });
    expect(toast.error).not.toHaveBeenCalledWith(
      expect.stringContaining('Command timeout')
    );
    expect(store.getState().tasks.inferenceStatus.inferencePhase)
      .toBe(InferencePhase.LOADING);
  });
});
