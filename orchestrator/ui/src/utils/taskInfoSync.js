const stringArray = (items) => (
  Array.isArray(items) ? items.map((item) => String(item ?? '')) : []
);

const numberOrDefault = (value, fallback) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const normalizeRecordTaskInfo = (taskInfo = {}) => ({
  taskNum: String(taskInfo.taskNum ?? '').trim(),
  taskName: String(taskInfo.taskName ?? '').trim(),
  taskType: String(taskInfo.taskType ?? '').trim(),
  taskInstruction: stringArray(taskInfo.taskInstruction),
  subtaskInstruction: stringArray(taskInfo.subtaskInstruction),
  policyPath: String(taskInfo.policyPath ?? '').trim(),
  recordInferenceMode: Boolean(taskInfo.recordInferenceMode),
  controlHz: numberOrDefault(taskInfo.controlHz ?? 100, 100),
  inferenceHz: numberOrDefault(taskInfo.inferenceHz ?? 15, 15),
  chunkAlignWindowS: numberOrDefault(taskInfo.chunkAlignWindowS ?? 0.3, 0.3),
  includeRobotisLicense: Boolean(taskInfo.includeRobotisLicense),
  serviceType: String(taskInfo.serviceType ?? '').trim(),
  inferenceMode: String(taskInfo.inferenceMode ?? 'simulation').trim(),
});

export const getRecordTaskInfoKey = (taskInfo = {}) =>
  JSON.stringify(normalizeRecordTaskInfo(taskInfo));

export const rosTaskInfoToUiTaskInfo = (taskInfo = {}) => ({
  taskNum: taskInfo.task_num || '',
  taskName: taskInfo.task_name || '',
  taskType: taskInfo.task_type || '',
  taskInstruction: taskInfo.task_instruction || [],
  subtaskInstruction: taskInfo.subtask_instruction || [],
  policyPath: taskInfo.policy_path || '',
  recordInferenceMode: Boolean(taskInfo.record_inference_mode),
  serviceType: taskInfo.service_type || 'lerobot',
  inferenceMode: taskInfo.inference_mode || 'simulation',
  userId: taskInfo.user_id || '',
  controlHz: taskInfo.control_hz || 100,
  inferenceHz: taskInfo.inference_hz || 15,
  chunkAlignWindowS: taskInfo.chunk_align_window_s || 0.3,
  includeRobotisLicense: Boolean(taskInfo.include_robotis_license),
  warmupTime: taskInfo.warmup_time_s || 0,
  episodeTime: taskInfo.episode_time_s || 0,
  resetTime: taskInfo.reset_time_s || 0,
  numEpisodes: taskInfo.num_episodes || 0,
  pushToHub: Boolean(taskInfo.push_to_hub),
  privateMode: Boolean(taskInfo.private_mode),
  useOptimizedSave: Boolean(taskInfo.use_optimized_save_mode),
  recordRosBag2: Boolean(taskInfo.record_rosbag2),
});
