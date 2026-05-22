^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package cyclo-ui
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

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
