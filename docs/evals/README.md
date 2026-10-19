# Design evals

Arav's loop D, in shadow mode: one record per release of how quorus.dev and the
Quorum view measure, so design regressions show up as numbers next to the
screenshots, release after release.

Run it on the release machine (Chrome and Node present), from the repo root:

```
python scripts/site_evals.py            # label = the version in pyproject.toml
python scripts/site_evals.py --label 0.6.0-rc1 --skip-quorum
```

What it does:

- builds `website/`, serves the build, runs Lighthouse mobile and desktop, and
  takes full-page screenshots at 1440 and 390;
- starts a throwaway relay on a free port with a secret made for that run,
  seeds a demo room (the recording's cast: `maya`, `maya-claude`, `maya-codex`,
  `maya-gemini`, `maya-opencode`), samples the Quorum view's fps in a headed
  Chrome window (a real display; headless Chrome paces rAF at ~20 Hz) and takes
  a 4K still plus a reduced-motion frame;
- writes `docs/evals/<label>/summary.json` and `summary.md` (commit these) and
  the PNG and Lighthouse JSON artefacts to `.evals/<label>/` (git-ignored).

Heavy steps run under the suite lock (`/usr/bin/lockf /tmp/quorus-full-suite.lock`),
so an eval never runs beside a test suite. The relay is stopped by its own PID.

Record-only for two releases. The thresholds a later gate would use are written
into each record (Lighthouse ≥ 99, fps ≥ 60, CLS 0) but nothing fails on them
until there is data from two releases and Arav has seen it.
