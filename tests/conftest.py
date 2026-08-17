"""Tests own their environment: a dedicated app database, never dev's.

The env vars must be set before any quantrank500 module is imported, which is
why they live at the top of this root conftest.
"""

import os

os.environ.setdefault("QR500_ENV", "test")
os.environ.setdefault(
    "QR500_PG_DSN", "postgresql://quantrank:quantrank@localhost:5432/quantrank500_test"
)
# the lake stays shared (read-only market data); QR500_LAKE_DSN keeps its default


def pytest_configure(config):
    try:
        from quantrank500.config import APP_DSN, ensure_database

        ensure_database(APP_DSN)
    except Exception:
        pass  # Postgres down: integration tests skip themselves per-fixture
