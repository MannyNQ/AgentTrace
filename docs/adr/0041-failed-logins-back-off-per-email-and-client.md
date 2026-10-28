# 41. Failed logins back off per email and client, and are capped per client

Status: accepted. Replaces the login limit in design.md's Phase 7 (ten failures per email in 15 minutes, then 429).

## Context

The old limit counted failures per email only, and refused even the right password once it was reached. Anyone who knew the owner's email could keep them locked out by sending ten bad logins every 15 minutes, and an attacker who spread guesses across many emails was never limited at all (#23).

## Decision

- Failures are counted per email and client address. The first five are free; each one after that holds that email from that address for twice as long as the last, starting at 1 second and capped at 15 minutes. While held, a login from that address is refused with 429 before any hash, even with the right password, and the error says how long to wait. Holding one address never holds another, so an attacker's failures don't lock the owner out.
- Failures are also counted per client address across emails: 50 in 15 minutes turns that address away with 429. This stops credential stuffing and floods of random-email logins, which also fill the hashing queue.
- A successful login clears that email's count and hold from that address only, so the owner signing in doesn't hand an attacker elsewhere fresh free tries. `agenttrace-admin reset-password` clears them from every address. Neither clears the per-client count, so an attacker can't reset it by signing in to an account of their own.
- The web app sends the browser's address in `AgentTrace-Client-IP`, read from `X-Forwarded-For` at `AGENTTRACE_PROXY_HOPS` from the right (default 1). Next only sets that header when a request arrives without one, so the trustworthy entry is the one the nearest reverse proxy appended.
- Ingest believes `AgentTrace-Client-IP` only from peers in `AGENTTRACE_TRUSTED_FORWARDERS` (default: loopback and private ranges, where the web app reaches it from). From anyone else, the peer's own address is the client.
- The counts live in Redis: one hash per email holding each address's count and hold, so clearing an email is one delete, plus a counter per address. While Redis can't be reached, signing in answers 503, as before.

## Consequences

- Without a reverse proxy in front of the web app, a browser that sends its own `X-Forwarded-For` chooses the address it's counted under. The per-email holds still slow its guessing at any one address, and the README tells teams to run a proxy.
- Ingest exposed on a host where published ports hide the caller's address behind a private gateway (Docker Desktop, OrbStack) treats every direct caller as a forwarder. Narrow `AGENTTRACE_TRUSTED_FORWARDERS` to the web app's address there.
- A botnet can still guess against one email from many addresses; with argon2id and 12-character passwords, that's slow. A per-email cap across addresses would bring back the lockout this replaces.
- People behind one NAT share the per-client count, so 50 failures from a large office in 15 minutes turns it away for the rest of the window.
