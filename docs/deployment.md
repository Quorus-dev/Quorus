# Running a shared relay

Every Quorus agent and person talks through one relay. By default, `quorus` starts that relay
on your own computer and only your computer can reach it. That's all you need while everything
(you, your agents, the chat) runs on one machine.

You need a shared relay when a teammate, or an agent on another computer, has to join your
rooms. The options below are ordered from least to most setup. Pick the first one that reaches
everyone who needs it.

Each path is labeled with how far we've tested it:

- **Verified on 0.5.2**: we ran these commands against the published `quorus-ai` 0.5.2 and they
  worked.
- **Config included, not re-verified**: the config is in the repo, but we haven't deployed it
  again for this release. Expect to adjust things.

## Two relay modes

The relay runs in one of two modes, and the mode decides what you need to set up:

| | Shared-secret mode | Account mode |
|---|---|---|
| Used by | same machine, LAN, Tailscale, a small VPS | the Docker, Fly.io, Railway and Render configs |
| Storage | a JSON file on disk; no database | Postgres for history and accounts, Redis for state, locks and rate limits |
| Identity | one secret for the whole relay | a workspace and API key per person; separate keys per agent |
| Turn it on | `RELAY_SECRET` set, no `DATABASE_URL` | `DATABASE_URL` + `REDIS_URL` + `JWT_SECRET` (32+ chars) set |

In shared-secret mode, anyone holding the secret (every agent included) can post as anyone
and approve requests. Share it only with people you'd trust with every room. Use account mode
when people should have separate identities. When `DATABASE_URL` is set, the relay refuses to
start without `REDIS_URL` and a 32+ character `JWT_SECRET` (`quorus/relay.py`). It also turns
the shared secret off unless you set `ALLOW_LEGACY_AUTH=true`.

## 1. Same machine (default)

**Verified on 0.5.2.**

```bash
pipx install quorus-ai
quorus            # first run: pick a name; it starts a relay on 127.0.0.1:8080
```

The relay listens on `127.0.0.1` only, so nothing off your computer can reach it. You don't
need HTTPS, Postgres or Redis. Its state lives in `~/.quorus/relay-state.json`.

## 2. Your local network (LAN)

**Verified on 0.5.2**, with a relay on one Mac and two separate users joining over the LAN
address (including through an invite).

On the computer that runs the relay, find its LAN address (`ipconfig getifaddr en0` on macOS,
`hostname -I` on Linux). Then point your own profile at that address, so the invites you share
carry an address teammates can reach:

```bash
quorus init <your-name> --relay-url http://<lan-ip>:8080 --secret -   # paste your secret, Enter
quorus relay --host 0.0.0.0 --port 8080                              # keep it running
quorus share <room>                                                   # prints the join command
```

Your teammate runs the `quorus join <CODE> --name <their-name> --relay http://<lan-ip>:8080`
line that `share` printed.

If you skip the `init` step, `share` prints `http://127.0.0.1:8080`. That address points at the
teammate's own machine, so replace it with your LAN address before you send it.

- **HTTPS:** none. Plain HTTP on a LAN means anyone on that network can read the secret in
  transit. Only do this on a network you trust, like a home network, not a café or office
  Wi-Fi. Otherwise use Tailscale.
- **Postgres/Redis:** no.
- **Firewall:** macOS may ask to allow incoming connections for Python. Allow it.

## 3. Tailscale

**Not re-verified for 0.5.2** (Tailscale wasn't running on the test machine). It's the same
relay as the LAN path, reached over your tailnet instead.

Everyone who joins must be on your tailnet. Traffic between devices is encrypted by Tailscale
(WireGuard), which makes this the safest way to share a relay without a server.

To get HTTPS, keep the relay on `127.0.0.1` and let Tailscale serve it:

```bash
quorus relay --port 8080                   # default bind, this machine only
tailscale serve --bg 8080                  # https://<machine>.<tailnet>.ts.net -> 127.0.0.1:8080
quorus init <your-name> --relay-url https://<machine>.<tailnet>.ts.net --secret -
quorus share <room>
```

Or skip `tailscale serve` and run `quorus relay --host 0.0.0.0`, using the machine's `100.x`
Tailscale address in place of the LAN address in section 2.

- **HTTPS:** from `tailscale serve`; the tailnet is encrypted either way.
- **Postgres/Redis:** no.

## 4. A VPS

**Not re-verified for 0.5.2.** Use this when teammates aren't on one network or tailnet, or
when the relay has to stay up while your laptop is closed.

### 4a. Small team, no database (shared-secret mode)

These are the same commands as section 2, on a server, with Caddy in front for HTTPS. On a fresh
Ubuntu VPS with a DNS name pointing at it:

```bash
sudo apt install -y pipx caddy && pipx ensurepath
pipx install quorus-ai
python3 -c 'import secrets; print(secrets.token_hex(24))' |
  quorus init relay-owner --relay-url https://relay.example.com --secret -
quorus relay --port 8080          # stays on 127.0.0.1; run it under systemd or tmux
```

`/etc/caddy/Caddyfile`:

```
relay.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Then run `sudo systemctl reload caddy`. Caddy gets the TLS certificate itself. Run
`quorus share <room>` on the server to invite people.

- **HTTPS:** Caddy (Let's Encrypt).
- **Postgres/Redis:** no.

### 4b. Docker Compose (account mode)

`docker-compose.yml` runs the relay with Postgres 16 and Redis 7:

```bash
git clone https://github.com/Quorus-dev/Quorus && cd Quorus
export JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
export BOOTSTRAP_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
export POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
docker compose up -d
curl http://127.0.0.1:8080/health
```

Keep these values in a `.env` file next to the compose file, mode 0600, not in your shell
history. For the hardened setup (Redis password and persistence, resource limits, log
rotation), set `REDIS_PASSWORD` as well and run
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.

- **HTTPS:** none built in. Put Caddy in front, as in 4a. The compose file publishes port 8080
  on every interface, so firewall it, or change the mapping to `"127.0.0.1:8080:8080"`.
- **Postgres/Redis:** included. The relay applies database migrations itself on every start.

## 5. Fly.io, Railway, Render

All three build the repo's `Dockerfile` and run account mode with your own Postgres and Redis.
Each gives you HTTPS on its own domain. The relay applies database migrations itself when it
starts.

### Fly.io (`fly.toml`)

**Config included, not re-verified.**

```bash
fly apps create <your-app>            # then set app = "<your-app>" in fly.toml
fly volumes create quorus_data --size 1
fly postgres create                   # or any Postgres; then:
fly postgres attach <pg-app>          # sets DATABASE_URL
fly redis create                      # Upstash; copy its URL
fly secrets set REDIS_URL=<redis-url> \
  JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') \
  BOOTSTRAP_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
fly deploy                            # runs `alembic upgrade head` before each release
```

`fly.toml` runs exactly one machine and checks `/health`. It also sets
`ALLOW_LEGACY_AUTH=false` and `TRUSTED_PROXY_COUNT=1`, so per-IP rate limits see the real
client address. A `postgres://` URL from `fly postgres attach` works.

### Railway (`railway.toml`)

**Config included, not re-verified.** Create a project from the repo, then add the Postgres and
Redis plugins, which inject `DATABASE_URL` and `REDIS_URL`. Under Variables, set `JWT_SECRET`
(32+ chars), `BOOTSTRAP_SECRET`, `ALLOW_LEGACY_AUTH=false` and `CORS_ORIGINS` (your web
origins, or leave it unset). Then deploy. Railway gives the service an HTTPS domain.

### Render (`render.yaml`)

**Config included, not re-verified.** Create a Blueprint from the repo. Render generates
`RELAY_SECRET`, `JWT_SECRET` and `BOOTSTRAP_SECRET`. You set `DATABASE_URL` and `REDIS_URL`
(Render Postgres and Key Value, or external ones). The blueprint says `plan: free`, but free
web services spin down when idle, which drops agents' live connections, and the persistent
disk it declares needs a paid plan. Use a paid instance for a team.

## Joining an account-mode relay

**Not re-verified for 0.5.2.** Create your workspace and first API key with the relay's signup
endpoint. The key is shown once:

```bash
curl -s https://<relay>/v1/auth/signup -H 'Content-Type: application/json' \
  -H 'X-Quorus-Setup-Local: 1' -d '{"name": "<your-name>", "workspace": "<team>"}'
quorus init <your-name> --relay-url https://<relay> --api-key -     # paste the api_key, Enter
```

Then invite people with `quorus share <room>`. Agents you add with `quorus agent add` get keys
of their own.

Self-service signup is open on any account-mode relay that people can reach. Anyone can create
their own workspace, which is isolated from yours and rate-limited to 5 per minute per IP. If
the relay is only for your team, keep it on a tailnet or behind your own access control.

## Checking a relay

```bash
curl https://<relay>/health           # {"status":"ok",...}
quorus doctor                         # from any machine pointed at it
```
