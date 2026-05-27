^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package cyclo-ui
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.6 (2026-05-27)
------------------
* Reworked BT Manager with drag-and-drop node palette, dynamic node catalog loading, and parameter editing.
* Added behavior tree XML serialization, save-as handling, tree list modal, undo/redo history, and parser tests.
* Added persistent BT runtime flow and improved node drop positioning.
* Optimized RobotViewer3D ROS subscriptions by sharing rosbridge connections, lowering queue depth, and gating action preview subscriptions.
* Contributors: kimtaehyeong99, Seongoo

0.1.5 (2026-05-26)
------------------
* Added single-task episode recording and save support when no subtask count is configured.
* Preserved numeric task IDs when dispatching recording commands from the UI.
* Enabled episode discard during active single-task and segmented recording flows.
* Contributors: kimtaehyeong99

0.1.4 (2026-05-22)
------------------
* Fixed URDF loading fallbacks for the 3D robot viewer.
* Added LeRobot and GR00T target backend selection for Hugging Face model downloads.
* Routed model download folder browsing to the selected policy checkpoint dropbox.
* Contributors: Dongyun Kim

0.1.3 (2026-05-22)
------------------
* None

0.1.2 (2026-05-20)
------------------
* Added Cyclo Manager shortcuts and navigation.
* Added light/dark theme controls and supporting UI styles.
* Fixed dataset conversion folder selection so nested paths such as Temp/<dataset> are preserved.
* Set Hugging Face upload/download folder browser defaults to /workspace.
* Added replay segment restore/save support backed by timestamp-based episode_info.json segments.
* Contributors: kimtaehyeong99

0.1.1 (2026-05-15)
------------------
* None

0.1.0 (2026-05-15)
------------------
* Initial open-source release of the Cyclo Intelligence web UI.
* Added pages and controls for recording, inference, training, dataset tools, and robot status monitoring.
* Added ROS bridge integration for orchestrator and cyclo_data services.
* Contributors: ROBOTIS
