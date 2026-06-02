export const normalizeRecordTaskInfo = (taskInfo = {}) => ({
  taskNum: String(taskInfo.taskNum ?? '').trim(),
  taskName: String(taskInfo.taskName ?? '').trim(),
  taskInstruction: Array.isArray(taskInfo.taskInstruction)
    ? taskInfo.taskInstruction.map((item) => String(item ?? ''))
    : [],
  subtaskInstruction: Array.isArray(taskInfo.subtaskInstruction)
    ? taskInfo.subtaskInstruction.map((item) => String(item ?? ''))
    : [],
  includeRobotisLicense: Boolean(taskInfo.includeRobotisLicense),
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
