# 34. Claiming an install with a setup token

Status: accepted. Builds on ADR-0002 and ADR-0032.

## Context

A fresh or upgraded install has data and keys but no users. The first account becomes the owner of the default org. If any visitor could create that account, whoever reached a newly exposed instance first could claim it.

## Decision

- While no user exists, the web app shows a one-time "create the owner account" screen that asks for a setup token.
- The token is `TRAYCE_SETUP_TOKEN` when set, or a random token ingest generates and prints in its logs on startup while the install is unclaimed.
- Claiming the install creates the owner of the default org. Existing projects, keys, and data stay as they are, under the default org and project.
- Members join by invite links and recover passwords by reset links, both created by an owner: single use, 7-day expiry, with the role chosen in the invite. There is no email.
- A locked-out owner recovers with `docker compose exec ingest trayce-admin reset-password <email>`.
- The org always keeps at least one owner; the last owner can't be demoted or removed.
- Deactivating a user voids every unused link they created and every reset link aimed at them, in the same transaction, so a removed owner can't come back through an invite they made earlier.

## Consequences

- Claiming an install needs access to its logs or its environment, which an operator has and a passer-by doesn't.
- SMTP can come later as another way to deliver the same invite and reset links.
