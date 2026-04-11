"""Set required environment variables before any test module imports."""

import os

os.environ.setdefault("RELAY_SECRET", "test-secret")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("BOOTSTRAP_SECRET", "test-bootstrap-secret")
