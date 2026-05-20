# GR00T Checkpoints

This folder is bind-mounted into the GR00T container at:

```text
/policy_checkpoints/groot
```

Put user-trained GR00T policy checkpoints here when you want to test them
without copying files into the container manually.

Use this container path when calling `InferenceCommand.LOAD`:

```text
/policy_checkpoints/groot/<model-or-checkpoint>
```

Model artifacts are intentionally ignored by git.
