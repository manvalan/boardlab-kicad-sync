"""BoardLab Sync toolbar button: pull-changes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import launcher

launcher.run("pull-changes")
