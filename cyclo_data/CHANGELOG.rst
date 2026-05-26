^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package cyclo_data
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.4 (2026-05-22)
------------------
* Changed the default Hugging Face model download path to the LeRobot policy checkpoint dropbox.
* Contributors: Dongyun Kim

0.1.3 (2026-05-22)
------------------
* Fixed VideoRecorder MJPEG pipe finalization so the last real camera frame is preserved without muxing a fake gray trailer frame.
* Removed ffmpeg ``+nobuffer`` from the recording pipe because it could drop the final MJPEG packet and leave MP4 frame counts one behind timestamp sidecars.
* Added regression coverage for recorder trailer handling and raw video/sidecar frame-count parity.
* Contributors: kimtaehyeong99

0.1.2 (2026-05-20)
------------------
* Optimized LeRobot video conversion by streaming selected MP4 frames directly into ffmpeg instead of writing temporary JPEG sequences.
* Reused synced-video cache entries with stronger frame-index, source-file, fps, resize, and rotation validation.
* Cached video statistics during streaming sync to avoid later random MP4 sampling when possible.
* Shared parsed episode data when converting LeRobot v2.1 and v3.0 together to avoid duplicate rosbag parsing.
* Added a fast path for single-episode LeRobot v3.0 video aggregation.
* Added timestamp-based replay segments in episode_info.json and restored them in replay data responses.
* Stopped creating robot_config.yaml when saving replay-only segment annotations unless an existing robot config is being updated.
* Simplified new recording episode_info.json metadata by removing unused recorder/transcoding bookkeeping fields.
* Added regression tests for streaming video sync frame counts, duplicate frames, resize/rotation, fallback behavior, and cache invalidation.
* Added regression tests for timestamp-based replay segment loading, legacy frame conversion, malformed segment skipping, and save behavior.
* Contributors: kimtaehyeong99

0.1.1 (2026-05-15)
------------------
* Stabilized VideoRecorder shutdown and resource lifecycle for repeated START/STOP recording.
* Hardened background transcode worker shutdown.
* Fixed LeRobot v3.0 aggregated video timing and frame-count validation.
* Fixed video_sync H.264 frame extraction by regenerating monotonic PTS.
* Added output-adjacent temporary disk controls for video sync.
* Corrected global statistics calculation and standard-deviation flooring for LeRobot normalization.
* Added regression tests for recorder resources, video sync, and v3.0 aggregation.
* Contributors: kimtaehyeong99

0.1.0 (2026-05-15)
------------------
* Initial open-source release of the Cyclo Intelligence data package.
* Added ROS 2 services and CLI tools for recording, conversion, dataset editing, visualization, and Hugging Face operations.
* Contributors: kimtaehyeong99
