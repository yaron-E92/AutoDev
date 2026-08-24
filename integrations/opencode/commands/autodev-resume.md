---
description: Resume an AutoDev issue-to-PR run with deterministic Python coordination
agent: build
subtask: false
---
The deterministic AutoDev Python coordinator validates the durable checkpoint and resumes before this prompt is sent. Its bounded progress/final output is below:

!`autodev coordinate --resume --arguments "$ARGUMENTS" --interactive-consent`

Return only the final JSON object from the output above. Do not call tools, reconstruct progress, continue the workflow, or merge anything. Workflow correctness and continuation selection are owned entirely by Python; this model turn is display-only.