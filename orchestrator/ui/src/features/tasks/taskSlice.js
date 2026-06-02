/*
 * Copyright 2025 ROBOTIS CO., LTD.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Author: Kiwoong Park
 */

import { createSlice } from '@reduxjs/toolkit';
import { RecordPhase, InferencePhase } from '../../constants/taskPhases';
import { getRecordTaskInfoKey } from '../../utils/taskInfoSync';

const SYNCED_MESSAGE = 'Session task info synced.';
const CONFLICT_MESSAGE = 'Server task info changed while editing; local draft not synced.';
const FAILED_MESSAGE = 'Task info not synced; robot button may use old task.';

const syncInitialState = {
  serverTaskKey: '',
  editBaseServerTaskKey: '',
  dirty: false,
  conflict: false,
  syncStatus: 'idle',
  syncMessage: '',
  serverTaskInfo: null,
};

const applyRecordTaskInfoToForm = (state, taskInfo) => {
  state.taskInfo = { ...state.taskInfo, ...taskInfo };
  const subtasks = Array.isArray(taskInfo.subtaskInstruction)
    ? taskInfo.subtaskInstruction
    : [];
  state.plannedSubTasks = subtasks;
  state.plannedCount = subtasks.length;
  state.slotToServerIdx = subtasks.map((_, index) => (
    Number.isInteger(state.slotToServerIdx[index])
      ? state.slotToServerIdx[index]
      : -1
  ));
  state.activeSlotIndex = subtasks.length > 0
    ? Math.min(state.activeSlotIndex, subtasks.length - 1)
    : 0;
};

const initialState = {
  // Hoisted shared field — same value drives both flows. Owned by
  // SetRobotType + the latest snapshot from either status topic.
  robotType: '',

  taskInfo: {
    taskNum: '',
    taskName: '',
    taskType: '',
    taskInstruction: [],
    subtaskInstruction: [],
    policyPath: '',
    recordInferenceMode: false,
    controlHz: 100,
    inferenceHz: 15,
    chunkAlignWindowS: 0.3,
    // Off by default — recording outputs are user-owned, not ROBOTIS'.
    // Tick on at the Record page when the dataset is a ROBOTIS internal
    // capture; the recorder then bakes the Apache 2.0 license header
    // into the task-folder README.
    includeRobotisLicense: false,
    // Inference backend (TaskInfo.service_type) + policy class. The two
    // are chosen together via the Model dropdown — see
    // components/InferenceModelSelector.js MODEL_OPTIONS for the list.
    // Empty serviceType falls back to orchestrator's config.json type
    // detection (backward-compat). policyType is UI-only (drives instruction
    // visibility via constants/policyCapabilities.js).
    serviceType: 'lerobot',
    policyType: 'act',
  },

  taskInfoSync: { ...syncInitialState },

  // Record-side snapshot from /data/recording/status (cyclo_data direct,
  // 5 Hz during a recording session). Conversion progress is its own
  // flow on /data/status (DataOperationStatus, OP_CONVERSION) routed
  // through editDatasetSlice.conversionStatus — distinct from this
  // record-session state.
  recordStatus: {
    taskName: 'idle',
    running: false,
    recordPhase: RecordPhase.READY,
    progress: 0,
    encodingProgress: 0,
    proceedTime: 0,
    currentEpisodeNumber: 0,
    currentScenarioNumber: 0,
    currentTaskInstruction: '',
    currentSubtaskIndex: 0,
    subtaskCount: 0,
    currentSubtaskInstruction: '',
    subtaskInstructions: [],
    userId: '',
    usedStorageSize: 0,
    totalStorageSize: 0,
    usedCpu: 0,
    usedRamSize: 0,
    totalRamSize: 0,
    topicReceived: false,
  },

  // Inference-side snapshot from /task/inference_status (orchestrator
  // direct, one-shot per phase transition).
  inferenceStatus: {
    inferencePhase: InferencePhase.READY,
    error: '',
    topicReceived: false,
  },

  availableRobots: [],
  availableCameras: [],
  policyList: [],
  datasetList: [],
  heartbeatStatus: 'disconnected',
  lastHeartbeatTime: 0,
  joystickMode: '',
  // Per-topic live monitor snapshot from rosbag_recorder (1 Hz while recording).
  recordingMonitor: {
    topics: [],         // [{name, rateHz, baselineHz, secondsSinceLast, status}]
    totalReceived: 0,
    totalWritten: 0,
  },

  plannedCount: 0,
  plannedSubTasks: [],
  slotToServerIdx: [],
  activeSlotIndex: 0,
};

const taskSlice = createSlice({
  name: 'tasks',
  initialState,
  reducers: {
    setTaskInfo: (state, action) => {
      state.taskInfo = { ...state.taskInfo, ...action.payload };
    },
    resetTaskInfo: (state) => {
      state.taskInfo = initialState.taskInfo;
      state.taskInfoSync = { ...syncInitialState };
    },
    setRecordStatus: (state, action) => {
      state.recordStatus = { ...state.recordStatus, ...action.payload };
    },
    resetRecordStatus: (state) => {
      state.recordStatus = initialState.recordStatus;
    },
    setInferenceStatus: (state, action) => {
      state.inferenceStatus = { ...state.inferenceStatus, ...action.payload };
    },
    resetInferenceStatus: (state) => {
      state.inferenceStatus = initialState.inferenceStatus;
    },
    selectRobotType: (state, action) => {
      state.robotType = action.payload;
    },
    setTaskType: (state, action) => {
      state.taskInfo.taskType = action.payload;
    },
    setTaskInstruction: (state, action) => {
      state.taskInfo.taskInstruction = action.payload;
    },
    setPolicyPath: (state, action) => {
      state.taskInfo.policyPath = action.payload;
    },
    setRecordInferenceMode: (state, action) => {
      state.taskInfo.recordInferenceMode = action.payload;
    },
    setHeartbeatStatus: (state, action) => {
      state.heartbeatStatus = action.payload;
    },
    setLastHeartbeatTime: (state, action) => {
      state.lastHeartbeatTime = action.payload;
    },
    setJoystickMode: (state, action) => {
      state.joystickMode = action.payload || '';
    },
    setRecordingMonitor: (state, action) => {
      state.recordingMonitor = action.payload;
    },
    setPlannedCount: (state, action) => {
      state.plannedCount = action.payload;
    },
    setPlannedSubTasks: (state, action) => {
      state.plannedSubTasks = action.payload;
      state.taskInfo.subtaskInstruction = action.payload;
    },
    setPlannedSubTaskAt: (state, action) => {
      const { index, value } = action.payload;
      if (index >= 0 && index < state.plannedSubTasks.length) {
        state.plannedSubTasks[index] = value;
        state.taskInfo.subtaskInstruction = state.plannedSubTasks;
      }
    },
    setSlotToServerIdx: (state, action) => {
      state.slotToServerIdx = action.payload;
    },
    setActiveSlotIndex: (state, action) => {
      state.activeSlotIndex = action.payload;
    },
    resetSegmentPlan: (state) => {
      state.plannedCount = 0;
      state.plannedSubTasks = [];
      state.slotToServerIdx = [];
      state.activeSlotIndex = 0;
      state.taskInfo.subtaskInstruction = [];
    },
    resetSegmentProgress: (state) => {
      state.slotToServerIdx = state.plannedSubTasks.map(() => -1);
      state.activeSlotIndex = 0;
    },
    markLocalTaskInfoEdited: (state) => {
      if (!state.taskInfoSync.dirty) {
        state.taskInfoSync.editBaseServerTaskKey = state.taskInfoSync.serverTaskKey;
      }
      state.taskInfoSync.dirty = true;
      state.taskInfoSync.syncStatus = state.taskInfoSync.conflict ? 'conflict' : 'pending';
      state.taskInfoSync.syncMessage = state.taskInfoSync.conflict
        ? CONFLICT_MESSAGE
        : 'Task info changed; syncing soon...';
    },
    markTaskInfoSyncPending: (state) => {
      state.taskInfoSync.syncStatus = 'pending';
      state.taskInfoSync.syncMessage = 'Task info changed; syncing soon...';
    },
    markTaskInfoSyncing: (state) => {
      state.taskInfoSync.syncStatus = 'syncing';
      state.taskInfoSync.syncMessage = 'Syncing task info...';
    },
    markTaskInfoSyncSuccess: (state) => {
      const taskKey = getRecordTaskInfoKey(state.taskInfo);
      state.taskInfoSync.serverTaskKey = taskKey;
      state.taskInfoSync.editBaseServerTaskKey = taskKey;
      state.taskInfoSync.dirty = false;
      state.taskInfoSync.conflict = false;
      state.taskInfoSync.syncStatus = 'synced';
      state.taskInfoSync.syncMessage = SYNCED_MESSAGE;
      state.taskInfoSync.serverTaskInfo = {
        ...state.taskInfo,
        taskInstruction: [...(state.taskInfo.taskInstruction || [])],
        subtaskInstruction: [...(state.taskInfo.subtaskInstruction || [])],
      };
    },
    markTaskInfoSyncFailed: (state, action) => {
      state.taskInfoSync.syncStatus = 'failed';
      state.taskInfoSync.syncMessage = action.payload || FAILED_MESSAGE;
    },
    markTaskInfoSyncMissing: (state) => {
      state.taskInfoSync.syncStatus = 'missing';
      state.taskInfoSync.syncMessage = 'Fill Task Num and Task Name to sync.';
    },
    receiveServerRecordTaskInfo: (state, action) => {
      const serverTaskInfo = action.payload || {};
      const serverTaskKey = getRecordTaskInfoKey(serverTaskInfo);
      const localTaskKey = getRecordTaskInfoKey(state.taskInfo);
      if (
        serverTaskKey === state.taskInfoSync.serverTaskKey &&
        serverTaskKey === localTaskKey &&
        !state.taskInfoSync.dirty &&
        !state.taskInfoSync.conflict &&
        state.taskInfoSync.syncStatus === 'synced'
      ) {
        return;
      }
      state.taskInfoSync.serverTaskKey = serverTaskKey;
      state.taskInfoSync.serverTaskInfo = serverTaskInfo;

      if (state.taskInfoSync.dirty) {
        if (serverTaskKey === localTaskKey) {
          state.taskInfoSync.editBaseServerTaskKey = serverTaskKey;
          state.taskInfoSync.dirty = false;
          state.taskInfoSync.conflict = false;
          state.taskInfoSync.syncStatus = 'synced';
          state.taskInfoSync.syncMessage = SYNCED_MESSAGE;
        } else if (
          serverTaskKey !== state.taskInfoSync.editBaseServerTaskKey
        ) {
          state.taskInfoSync.conflict = true;
          state.taskInfoSync.syncStatus = 'conflict';
          state.taskInfoSync.syncMessage = CONFLICT_MESSAGE;
        }
        return;
      }

      if (serverTaskKey !== localTaskKey) {
        applyRecordTaskInfoToForm(state, serverTaskInfo);
      }
      state.taskInfoSync.editBaseServerTaskKey = serverTaskKey;
      state.taskInfoSync.conflict = false;
      state.taskInfoSync.syncStatus = 'synced';
      state.taskInfoSync.syncMessage = SYNCED_MESSAGE;
    },
    applyServerTaskInfo: (state) => {
      if (!state.taskInfoSync.serverTaskInfo) return;
      applyRecordTaskInfoToForm(state, state.taskInfoSync.serverTaskInfo);
      state.taskInfoSync.editBaseServerTaskKey = state.taskInfoSync.serverTaskKey;
      state.taskInfoSync.dirty = false;
      state.taskInfoSync.conflict = false;
      state.taskInfoSync.syncStatus = state.taskInfoSync.serverTaskKey ? 'synced' : 'idle';
      state.taskInfoSync.syncMessage = state.taskInfoSync.serverTaskKey ? SYNCED_MESSAGE : '';
    },
  },
});

export const {
  setTaskInfo,
  resetTaskInfo,
  setRecordStatus,
  resetRecordStatus,
  setInferenceStatus,
  resetInferenceStatus,
  selectRobotType,
  setTaskType,
  setTaskInstruction,
  setPolicyPath,
  setRecordInferenceMode,
  setHeartbeatStatus,
  setLastHeartbeatTime,
  setJoystickMode,
  setRecordingMonitor,
  setPlannedCount,
  setPlannedSubTasks,
  setPlannedSubTaskAt,
  setSlotToServerIdx,
  setActiveSlotIndex,
  resetSegmentPlan,
  resetSegmentProgress,
  markLocalTaskInfoEdited,
  markTaskInfoSyncPending,
  markTaskInfoSyncing,
  markTaskInfoSyncSuccess,
  markTaskInfoSyncFailed,
  markTaskInfoSyncMissing,
  receiveServerRecordTaskInfo,
  applyServerTaskInfo,
} = taskSlice.actions;

export default taskSlice.reducer;
