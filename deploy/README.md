# Exposing the control plane

The UI was written on the assumption that it is only ever reachable from
loopback. Putting a public hostname in front of it invalidates that assumption,
so this directory records what replaces it and in what order.

## What protects what

| Layer | Protects against | Where |
|---|---|---|
| Loopback bind | Anything on the LAN reaching the app directly | `scripts/serve_public.py` |
| Cloudflare tunnel | Inbound connections; no port is opened on this host | `cloudflared-config.yml` |
| TLS at the edge | Session cookie in the clear | Cloudflare |
| Password gate | Anyone who reaches the hostname | `src/ads/api/auth.py` |
| Rate limit | Guessing the password at machine speed | `src/ads/api/auth.py` |

None of these is sufficient alone. The tunnel is not authentication -- the
hostname is public and anyone can resolve it. The password gate is the only
thing that distinguishes the owner from the internet.

## Order of operations

The order matters: the tunnel must not be started before the gate is on, or
there is a window where the control plane is public and unauthenticated.

1. **Set the credential.** Writes a scrypt hash to `.auth.env`, which is
   gitignored. The plaintext is not stored anywhere.

   ```
   python scripts/set_password.py --username <name>
   ```

2. **Start the gated server.** Refuses to start without step 1. Binds to
   127.0.0.1 only.

   ```
   python scripts/serve_public.py
   ```

3. **Verify the gate locally before exposing anything.** All three must hold:

   ```
   curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8077/api/runs    # 401
   curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8077/            # 303
   curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8077/api/health  # 200
   ```

4. **Authorize this host against the Cloudflare account.** Opens a browser;
   select the zone. Writes `~/.cloudflared/cert.pem`.

   ```
   cloudflared tunnel login
   ```

5. **Create the tunnel and route the hostname.**

   ```
   cloudflared tunnel create agentic-ds
   cloudflared tunnel route dns --overwrite-dns agentic-ds api.altspacelabs.com
   ```

6. **Install the config** from `cloudflared-config.yml` to `~/.cloudflared/config.yml`,
   substituting the UUID printed by `tunnel create`.

7. **Run it.**

   ```
   cloudflared tunnel run agentic-ds
   ```

8. **Verify from outside.** `/api/runs` must be 401 and `/` must redirect to
   `/login` when called without a cookie, from a machine that is not this one.

## A trap worth knowing about

Once `~/.cloudflared/config.yml` exists with a `tunnel:` key, **that name wins
over the one you pass on the command line**. `cloudflared tunnel info <other>`
returns information about the configured tunnel instead, silently and with no
warning. Verified here: asking for two unrelated tunnels by name both returned
`agentic-ds`.

The reason this matters is `tunnel delete`. Deleting "the old tunnel" by name
while a config file names a different one is a plausible way to destroy the
wrong tunnel and not find out until the hostname stops resolving.

Address tunnels by UUID and neutralise the config when doing anything
destructive:

```
printf 'ingress:\n  - service: http_status:404\n' > /tmp/neutral.yml
cloudflared --config /tmp/neutral.yml tunnel info <uuid>
cloudflared --config /tmp/neutral.yml tunnel delete <uuid>
```

## Known gaps

Recorded rather than hidden, because a deployment document that reads as
reassurance is worth less than one that reads as an accurate map.

- **No audit log.** A successful login is not recorded anywhere durable. There
  is no way to answer "did anyone else sign in" after the fact.
- **Single account, no rotation.** Changing the password means re-running
  `set_password.py`; there is no way to revoke one session without revoking
  all of them (the signing secret is rotated wholesale).
- **Rate limiting is in-process.** Restarting the server clears the lockout
  table, so a persistent guesser can reset their budget by waiting for a
  restart. Acceptable for a single-operator deployment, not for a shared one.
- **The tunnel is not access-controlled.** Cloudflare Access in front of this
  would add a second, independent factor and is the obvious next step if this
  ever holds data that is not the owner's own test fixtures.
