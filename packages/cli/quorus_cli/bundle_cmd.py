"""`quorus share-branch` and `quorus fetch`: hand a branch to teammates on other machines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quorus.bundle_client import (
    BundleClientError,
    bearer,
    download,
    fetch_into,
    list_bundles,
    make_bundle,
    upload,
)
from quorus.config import load_config


def _relay(cfg: dict[str, Any]) -> tuple[str, str]:
    relay_url = cfg["relay_url"]
    return relay_url, bearer(relay_url, api_key=cfg.get("api_key") or "",
                             secret=cfg.get("relay_secret") or "",
                             name=cfg.get("instance_name") or "")


def _repo(args: Any) -> Path:
    repo = Path(getattr(args, "repo", None) or ".").expanduser().resolve()
    if not (repo / ".git").exists():
        raise BundleClientError(f"not a git repo: {repo}")
    return repo


def cmd_share(args: Any, console: Any) -> None:
    try:
        repo = _repo(args)
        data, branch = make_bundle(repo, branch=args.branch, base=args.base, full=args.full)
        relay_url, bearer = _relay(load_config())
        meta = upload(relay_url, bearer, args.room, data)
    except BundleClientError as exc:
        console.print(f"[error]share:[/] {exc}")
        raise SystemExit(1) from None
    console.print(f"[success]✓[/] shared [accent]{branch}[/] @{meta['head'][:7]} "
                  f"({meta['size']} bytes) in #{args.room}")
    console.print(f"  teammates run: [accent]quorus fetch {meta['id']} --room {args.room}[/]")
    console.print("  [dim]it lasts 24h, and a relay restart drops it: share again if asked[/]")


def cmd_fetch(args: Any, console: Any) -> None:
    try:
        relay_url, bearer = _relay(load_config())
        if not args.bundle_id:
            rows = list_bundles(relay_url, bearer, args.room)
            if not rows:
                console.print(f"[dim]no shared bundles in #{args.room}[/]")
            for row in rows:
                console.print(f"  {row['id']}  @{row['uploader']}  {row['branch']} "
                              f"@{row['head'][:7]}  {row['size']} bytes")
            return
        repo = _repo(args)
        data, meta = download(relay_url, bearer, args.room, args.bundle_id)
        got = fetch_into(repo, data, meta)
    except BundleClientError as exc:
        console.print(f"[error]fetch:[/] {exc}")
        raise SystemExit(1) from None
    console.print(f"[success]✓[/] fetched @{got.uploader}'s {got.branch} into [accent]{got.ref}[/]")
    console.print(f"  review it: [accent]git show {got.head[:7]}[/]   "
                  f"[dim](no branch was created or checked out)[/]")


def cmd_release_name(args: Any, console: Any) -> None:
    """Free a claimed name on this machine's shared-secret relay (owner only:
    it edits the relay's own claims file, so it can't be done over the wire)."""
    from quorus.auth.participant_keys import ParticipantKeyStore, data_dir

    folder = Path(args.data_dir).expanduser() if args.data_dir else data_dir()
    if not (folder / "participant_keys.db").exists():
        console.print(f"[error]release-name:[/] no claims file in {folder}; "
                      "run this on the relay's machine (or pass --data-dir)")
        raise SystemExit(1)
    if ParticipantKeyStore(folder).release(args.name):
        console.print(f"[success]✓[/] released {args.name!r}; the next claim gets a new key")
    else:
        console.print(f"[dim]{args.name!r} was not claimed[/]")
