---
description: Read one AutoDev issue through the privacy-gated Python role runner
agent: build
subtask: false
---
The deterministic AutoDev Python role runner executes the isolated Reader before this display-only prompt is sent:

!`__AUTODEV_PYTHON_SHELL__ .opencode/autodev.py role --role reader --arguments "$ARGUMENTS"`

Return only the final JSON object above. Do not call tools, read repository files, continue to another role, or infer workflow state.
