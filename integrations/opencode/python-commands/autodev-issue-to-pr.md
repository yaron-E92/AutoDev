---
description: Run one AutoDev issue to PR with deterministic Python coordination
agent: build
subtask: false
---
The deterministic AutoDev Python coordinator runs before this prompt is sent. Its bounded progress/final output is below:

!`__AUTODEV_PYTHON_SHELL__ .opencode/autodev.py coordinate --arguments "$1"`

Return only the final JSON object from the output above. Do not call tools, continue the workflow, infer another stage, or merge anything. Workflow correctness and role sequencing are owned entirely by Python; this model turn is display-only.
