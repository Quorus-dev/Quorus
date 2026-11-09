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


def test_image_copies_every_file_the_wheel_force_includes():
    """The image didn't build: llms.txt and scripts/ are force-included into the
    wheel but were never copied in (found deploying Quorus Cloud, 2026-11-09)."""
    import tomllib

    dockerfile = (ROOT / "Dockerfile").read_text()
    copied = [word for line in dockerfile.splitlines() if line.startswith("COPY ")
              and "--from" not in line for word in line.split()[1:-1]]
    forced = tomllib.loads((ROOT / "pyproject.toml").read_text())[
        "tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    missing = [src for src in forced
               if not any(src == c or src.startswith(c.rstrip("/") + "/") for c in copied)]
    assert not missing, f"Dockerfile never copies: {missing}"
