^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package orchestrator
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

0.1.9 (2026-06-02)
------------------
* Synced prepared recording task information from SET_TASK_INFO so robot-button recordings use the latest folder name and metadata.
* Cached UI task information for trigger-started recording sessions and refreshed recording status after task info updates.
* Contributors: kimtaehyeong99

0.1.8 (2026-06-01)
------------------
* None

0.1.7 (2026-05-27)
------------------
* Moved leader trigger recording control into the orchestrator backend using right and left tact trigger events.
* Added prepared Record and Inference recording contexts so trigger input can start, save, and cancel recordings without UI command bridging.
* Kept inference recording state tied to actual recording start and stop responses.
* Contributors: kimtaehyeong99

0.1.6 (2026-05-27)
------------------
* Added dynamic behavior tree node discovery, catalog services, and XML tree listing.
* Added behavior tree node templates and XML serialization support for generated action and control nodes.
* Refactored behavior tree actions around joint control, SendCommand model routing, inference lifecycle handling, and monotonic timing.
* Added tree editing support for save-as workflows, execution completion state, and persistent runtime flow.
* Contributors: kimtaehyeong99, Seongoo

0.1.5 (2026-05-26)
------------------
* None

0.1.4 (2026-05-22)
------------------
* None

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
* Initial open-source release of the Cyclo Intelligence orchestrator package.
* Added ROS 2 orchestration for recording, inference, training, task commands, file browsing, and behavior tree workflows.
* Added service forwarding boundaries for cyclo_data-owned recording, conversion, editing, and Hugging Face operations.
* Contributors: ROBOTIS
