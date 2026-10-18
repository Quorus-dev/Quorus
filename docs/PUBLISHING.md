# Releasing quorus-ai

quorus-ai ships to PyPI by trusted publishing: pushing a `v*` tag runs
`.github/workflows/publish.yml` (environment `pypi`). It checks that the tag
matches the package version, builds, test-installs the wheel, then uploads it
with attestations. No API token is involved.

## Version bump

The version appears in nine places; change all of them (a test checks):

- `pyproject.toml` and `packages/{cli,mcp,sdk,tui}/pyproject.toml`
- `quorus/__init__.py` (`__version__`)
- `quorus/relay.py` (the FastAPI `version=`)
- `server.json`, twice: the server's `version` and the PyPI package's `version`

The tag also lists the release in the MCP Registry: the `mcp-registry` job in
`publish.yml` runs after PyPI has the release, checks that `server.json` matches
the tag, and publishes it with a pinned, checksum-verified `mcp-publisher` that
signs in with GitHub OIDC (no token, no human login). The registry checks the
`mcp-name:` comment at the top of the README, which PyPI serves. To bump
`mcp-publisher`, change its version and sha256 in the job together.

## Gate

1. Every security-relevant commit has a reviewer's GO.
2. The full suite passes on the release branch (`pytest`, check its exit code),
   and `ruff check .` is clean.
3. The built wheel installs into a fresh HOME and works as a new user and as
   an agent (`pipx install` from the wheel, `quorus init`, a room, an agent).
4. The PR to `main` is green, and a human merges it with a merge commit (not a
   squash, so provenance notes survive).
5. Tag `vX.Y.Z` on `main`, then check that `pipx install quorus-ai==X.Y.Z` works
   from PyPI.

## Done-list: the public face ships with the release

A release isn't done until everything a user sees matches what ships:

- [ ] `CHANGELOG.md` has the release, and the GitHub release notes say the same.
- [ ] The README's text, screenshots and command list match the shipped CLI.
      The PyPI page is the README, so it updates with the release.
- [ ] If the UI changed: the README GIFs are re-recorded from the real new UI
      (a scripted scene on a test relay, an honest caption, no tokens visible,
      checked frame by frame).
- [ ] quorus.dev is updated the same day.
