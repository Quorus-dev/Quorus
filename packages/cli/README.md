# murmur-cli

Command-line interface for [Murmur](https://github.com/Quorus-dev/Quorus) — the
`murmur` executable with subcommands (`init`, `join`, `say`, `inbox`, `rooms`,
`ps`, ...).

This package is a standalone slice of Murmur. It depends on `httpx` and `rich`,
and uses `murmur-sdk` for the underlying relay client.

## Install

```bash
pip install murmur-cli
```

## Usage

```bash
murmur --help
murmur inbox
murmur say <room> <message>
```

See the main Murmur repository for full documentation.
