# murmur-tui

Terminal UI hub for [Murmur](https://github.com/Aarya2004/murmur) — `murmur begin` opens a full-screen Rich interface for managing rooms, agents, and live chat.

This package is a standalone slice of Murmur. It only depends on `httpx` and
`rich`, and exposes the hub entry point plus the helpers that back it.

## Install

```bash
pip install murmur-tui
```

## Usage

```python
from murmur_tui import run_hub

run_hub()
```

See the main Murmur repository for full documentation.
