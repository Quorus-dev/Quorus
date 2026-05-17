# Contributing to Murmur

Thanks for your interest in contributing to Murmur!

## Development Setup

```bash
git clone https://github.com/Quorus-dev/Quorus.git
cd Quorus
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e packages/sdk -e packages/cli -e packages/mcp -e packages/tui
# macOS only: undo a hatchling-editable + Python 3.14 + macOS interaction
# that hides .pth files via com.apple.provenance + UF_HIDDEN. Idempotent.
bash scripts/fix_editable_pth.sh
python -c "import quorus, quorus_sdk, quorus_cli, quorus_mcp, quorus_tui"  # must print nothing
```

## Running Tests

```bash
pytest -v
```

## Running the E2E Test

```bash
python test_e2e_live.py
```

## Code Standards

- Python 3.10+, async-first
- Ruff for linting: `ruff check .`
- Max 100 char lines, files under 500 lines
- All external input validated before use
- Never log secrets or tokens
- Tests required for new features and bug fixes

## Commit Conventions

- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- Under 50 chars, imperative mood
- One logical change per commit

## Submitting Changes

1. Fork the repo and create a branch
2. Make your changes with tests
3. Run `ruff check .` and `pytest -v`
4. Submit a pull request

## Architecture

See `CONTEXT.md` for current architecture and design decisions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
