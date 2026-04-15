# Publishing to PyPI

## Prerequisites

1. PyPI account at https://pypi.org
2. API token (generate at https://pypi.org/manage/account/token/)
3. `uv` installed

## Pre-publish Checklist

```bash
# 1. Run all tests
uv run pytest tests/ -v
# Should see 148+ tests passing

# 2. Clean build
rm -rf dist/
uv build
# Creates dist/quorus-{version}.tar.gz and .whl

# 3. Test install in clean env
uv venv /tmp/publish-test
uv pip install --python /tmp/publish-test/bin/python dist/quorus-*.whl
/tmp/publish-test/bin/quorus --help
rm -rf /tmp/publish-test
```

## Publish

```bash
# Option 1: uv publish (recommended)
uv publish --token pypi-YOUR-API-TOKEN

# Option 2: twine
pip install twine
twine upload dist/*
```

## Post-publish Verification

```bash
# Wait 1-2 minutes for PyPI to index
pip install quorus
quorus --help
python -c "from quorus_sdk.http_agent import QuorusClient; print('OK')"
```

## Version Bump

Edit `quorus/__init__.py` and `pyproject.toml` version field, then rebuild and publish.

## TestPyPI (dry run)

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-YOUR-TEST-TOKEN
pip install -i https://test.pypi.org/simple/ quorus
```
