# Murmur Packages

This directory holds the modular packages that make up Murmur.

```
packages/
├── core/   # Relay server (FastAPI, auth, storage, routes, services, backends, migrations)
├── sdk/    # Client library (Room, MurmurClient) — depends only on httpx
├── mcp/    # MCP server integration (FastMCP tools)
├── cli/    # CLI commands (murmur init, join, say, ...)
└── tui/    # Terminal UI hub (murmur begin)
```

## Installation

The umbrella package `murmur-ai` (root `pyproject.toml`) installs everything for
the standard developer experience:

```bash
pip install murmur-ai
```

Individual packages can also be installed for slim deployments:

```bash
pip install murmur-sdk    # just the client
pip install murmur-cli    # CLI + SDK
pip install murmur-core   # relay server only
```

## Backward compatibility

The legacy `murmur/` directory remains as a thin re-export shim so that existing
code like `from murmur import Room` and `from murmur.integrations.http_agent import
MurmurClient` keeps working. New code should import from the package-specific
namespaces (`murmur_sdk`, `murmur_core`, etc.).

## Layout convention

Each package follows the same shape:

```
packages/<name>/
├── pyproject.toml       # build config, deps, console_scripts
├── README.md            # what this package does
├── murmur_<name>/       # the actual Python package
│   └── __init__.py
└── tests/               # package-specific tests (optional; some live at repo root)
```

Builds use hatchling. The root workspace uses uv for development.
