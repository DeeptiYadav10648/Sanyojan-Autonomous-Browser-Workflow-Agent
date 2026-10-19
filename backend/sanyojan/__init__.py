"""Sanyojan's bounded, read-only browser workflow service."""

import os

# Keep browser dependencies inside the project virtualenv, not the user's HOME.
# Docker explicitly overrides this with its image-local browser installation.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

__version__ = "0.1.0"
