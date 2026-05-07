"""Deployment config regressions for Fly release commands."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_image_includes_alembic_config_for_release_command():
    dockerfile = (ROOT / "Dockerfile").read_text()
    fly_toml = (ROOT / "fly.toml").read_text()

    assert 'release_command = "alembic upgrade head"' in fly_toml
    assert "COPY --from=builder /build/alembic.ini /app/alembic.ini" in dockerfile
    assert (
        "COPY --from=builder /build/quorus/migrations /app/quorus/migrations"
        in dockerfile
    )
