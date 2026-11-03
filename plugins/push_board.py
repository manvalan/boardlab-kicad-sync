"""BoardLab Sync toolbar button: push-board."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import launcher

launcher.run("push-board")
