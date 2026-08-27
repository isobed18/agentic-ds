# Deploying on Railway

Railway rebuilds `main` on every push, so deployment is continuous by default.
This covers the parts it cannot infer: the variables to set, the volume, and the
two backends that cannot work there.

## Why a separate entry point

`scripts/serve_public.py` is the tunnel launcher. It binds loopback and reads
`.auth.env` and `.runtime.env` from beside the repository, and both are correct
there — cloudflared connects from the same host, and those files are written by
`set_users.py`.

Neither survives a container. The platform routes to a published port, so the
process must bind `0.0.0.0`, and configuration arrives as environment variables,
so there is no file to read. `scripts/serve_container.py` is that second entry
point. Loosening the tunnel launcher instead would have turned its loopback
guarantee into a flag somebody could switch off by accident.

## Variables

Set these in the service's **Variables** tab. The container refuses to start
without the first two, and says which one is missing.

| Variable | Value |
|---|---|
| `ADS_AUTH_USERS_JSON` | the JSON from `python scripts/set_users.py …` — hashes only, never passwords |
| `ADS_AUTH_SECRET` | any long random string; **rotating it logs everyone out** |
| `ADS_LLM_BACKEND` | `deepseek` |
| `DEEPSEEK_API` | the API key |
| `ADS_DEEPSEEK_USERS` | who may spend it; defaults to `ishak-ads` alone |
| `ADS_DEEPSEEK_RPM` | optional request budget, default 20/min |
| `ADS_TEAMS` | optional; unset means everyone shares one team |
| `ADS_DATA_DIR` | `/data`, and it must match the volume mount path |

Generate the accounts locally and paste the resulting `ADS_AUTH_USERS_JSON`
line — the passwords are printed once and only the scrypt hashes are stored:

```bash
python scripts/set_users.py gonenc-ads berkin-ads emre-ads ishak-ads
```

`ADS_AUTH_SECURE_COOKIE` needs no attention: Railway serves HTTPS, and secure
cookies are the default.

## The volume is not optional

Attach a volume mounted at **`/data`**.

A container filesystem is discarded on every deploy. Without a volume, uploaded
datasets, artifacts, automations and run state all disappear the next time
anything ships — and it will look like data loss rather than configuration,
because the app will be running perfectly.

## Backends

`claude_cli` drives an interactive Claude Code session logged in on somebody's
machine. `ollama` needs a model server. Neither exists in a container, so
`serve_container.py` refuses both at startup rather than letting every run fail
separately with a confusing error.

That leaves `deepseek`, which is **remote paid inference**, worth stating for a
project that describes itself as local-first. It is the testing arrangement, not
the destination: production is 8×H100 running local models, and `ollama` becomes
correct again there.

Document extraction is limited here on purpose. `documents-docling`,
`documents-unstructured` and the marker/mineru workers are not installed — they
add gigabytes and the project puts the heavy ones in isolated worker
environments by design. PDFs fall back to the built-in text-layer reader. Add
the extra to the `Dockerfile` when a deploy actually needs structure extraction.

## Health

`railway.json` sets the health check to `/api/health`, which is public by design
— it reports status and whether auth is configured, and nothing else. Every
other `/api/` route returns 401 without a session.

## If a build fails

```bash
railway login
railway link                 # pick the project and service
railway logs --build         # why the image did not build
railway logs                 # why the container did not start
railway variables            # what the service can actually see
```

The likely causes, in the order they occur:

1. **No Dockerfile detected** — Railway fell back to Nixpacks, which cannot know
   which entry point to run. `railway.json` pins `builder: DOCKERFILE`.
2. **Missing variables** — the container exits with a message naming the one it
   wants. That is a configuration failure, not a crash.
3. **No volume** — it will start and appear healthy, then lose everything on the
   next deploy. Check this before shipping anything real to it.
