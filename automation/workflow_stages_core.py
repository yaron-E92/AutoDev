from __future__ import annotations

# Compatibility import for historical internal references. New code must import
# automation.workflow_stages or the responsibility-specific workflow modules.
import sys
from automation import workflow_stages as _workflow_stages

sys.modules[__name__] = _workflow_stages
