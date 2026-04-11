# Contributing to Murmur

Thanks for your interest in contributing to Murmur!

## Development Setup

```bash
git clone https://github.com/Aarya2004/murmur.git
cd murmur
pip install -e ".[dev]"
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
