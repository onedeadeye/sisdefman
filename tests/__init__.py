import os
import tempfile

# Keep the GUI's list of recent projects out of the real user folder.
os.environ["SISDEFMAN_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sisdefman-test-config-")
