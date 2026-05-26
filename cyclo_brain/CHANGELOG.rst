^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package cyclo_brain
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.4 (2026-05-22)
------------------
* Added the shared two-process policy runtime with ``main-runtime`` and ``engine-process`` services.
* Refactored LeRobot and GR00T backends onto the shared runtime, replacing the backend-local inference server and control publisher split.
* Added schema-driven ``RobotClient`` runtime configuration for cameras, joint groups, sensor-backed state, and command publishers.
* Added action chunk buffering and command publishing through ``ActionChunkProcessor`` and ``RobotClient.publish_action``.
* Added LeRobot backend loading, IO mapping, preprocessing, prediction, and optimization modules for policy-container inference.
* Added GR00T N1.7 deployment support, camera mapping, smoke checks, and runtime tests.
* Added policy container Docker wiring, s6 service layout, SDK/runtime bind mounts, and checkpoint dropbox conventions.
* Added runtime architecture documentation, policy runtime contracts, fake robot publisher, and inference verification scripts.
* Fixed GR00T ``odometry`` action key routing to the configured ``mobile`` / ``/cmd_vel`` command topic.
* Contributors: Dongyun Kim

0.1.3 (2026-05-22)
------------------
* None

0.1.2 (2026-05-20)
------------------
* None

0.1.1 (2026-05-15)
------------------
* None

0.1.0 (2026-05-15)
------------------
* Initial open-source release of the Cyclo Intelligence policy backend workspace.
* Added policy backend, SDK, and runtime source layout for Cyclo Brain development.
* Contributors: Dongyun Kim
