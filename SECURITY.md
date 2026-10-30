# Security policy

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: open the [Security tab](https://github.com/MannyNQ/Trayce/security) and choose "Report a vulnerability". Don't open a public issue, pull request, or discussion for it.

Include what an attacker can do, the version or commit, and the steps to reproduce it. Once a fix ships, the advisory credits you unless you'd rather it didn't.

## Supported versions

Trayce is an alpha. Security fixes go into the latest release only; upgrade to get them. Ingest runs database migrations forward when it starts, so upgrading keeps your data.

| Version | Supported |
| --- | --- |
| Latest 0.x release | Yes |
| Earlier releases | No |

## What a replay trusts

`trayce replay` runs your agent, with its real tools, on what a trace recorded. Anyone with an `ingest` key can write a trace, so treat replay input as untrusted, and use `--stub-tools` or a sandbox when your agent's tools can do damage (ADR-0040). This is documented behavior, not a vulnerability.
