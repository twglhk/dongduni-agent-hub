import os
import tempfile

# Tests register workspaces; keep them out of the real ~/.config/agent-hub.
os.environ["AGENT_HUB_HOME"] = tempfile.mkdtemp(prefix="agent-hub-reg-")
