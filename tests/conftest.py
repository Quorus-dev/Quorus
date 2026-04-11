"""Set required environment variables before any test module imports."""

import os

os.environ.setdefault("RELAY_SECRET", "test-secret")
