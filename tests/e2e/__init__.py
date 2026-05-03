"""End-to-end test package — slow, opt-in, real-binary tests.

Tests in this directory are gated behind ``@pytest.mark.real_harness`` and
are NOT run by the default ``pytest`` invocation. Opt in with::

    pytest -m real_harness

See ``test_real_harness_e2e.py`` for the per-harness gate.
"""
