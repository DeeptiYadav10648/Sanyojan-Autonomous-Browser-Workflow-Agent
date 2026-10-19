"""Use project-local Chromium; the test command never downloads dependencies."""

import os

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
