"""dcdtuni agent layer: the slim trunk that reaches the platform through ``agent.scripted_api``.

The code lives in the trunk subpackages (``api``, ``tools``, ``scenarios``,
``optim``, ``policies``, ``records``, ``cli``). The pre-2026-10-22 tree
(``harness``, ``training``, ``analysis``, ``methods``, ``scenario_*``) was
frozen as ``src/agent_legacy`` and removed on 2026-10-22; it is kept at the
tag ``agent-pre-slim-2026-10-22``. Importing ``agent`` sets no environment
variable.
"""

__version__ = "1.0.0.dev0"
#: Version of the stable public surface ``agent.api`` (semantic versioning).
AGENT_API_VERSION = "1.0"

__all__ = ["AGENT_API_VERSION", "__version__"]
