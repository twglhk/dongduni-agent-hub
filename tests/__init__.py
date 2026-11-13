import os
import tempfile

# Tests register workspaces; keep them out of the real ~/.config/agent-hub.
os.environ["AGENT_HUB_HOME"] = tempfile.mkdtemp(prefix="agent-hub-reg-")
import os

os.environ.setdefault("DAH_SKIP_MODEL_CHECK", "1")  # `dah init` would otherwise spend a model call per run
