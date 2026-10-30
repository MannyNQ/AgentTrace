"""`trayce-admin`: operator commands run inside the ingest container, for what the API can't
do on its own, such as recovering an owner who forgot their password (ADR-0034).

    docker compose exec ingest trayce-admin reset-password <email>
"""

import argparse
import getpass
import sys

from trayce_ingest.auth import Auth
from trayce_ingest.auth_store import PostgresAuth, RedisLimiter
from trayce_ingest.errors import ApiError
from trayce_ingest.settings import Settings


def reset_password(a: Auth, email: str) -> int:
    password = getpass.getpass("New password: ")
    if getpass.getpass("Again: ") != password:
        print("the passwords don't match", file=sys.stderr)
        return 1
    try:
        user = a.set_password(email, password)
    except ApiError as e:
        print(e.error, file=sys.stderr)
        return 1
    print(f"password set for {user.email}; their sessions are signed out")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trayce-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset-password", help="set a user's password and sign them out everywhere"
    )
    reset.add_argument("email")
    args = parser.parse_args(argv)
    s = Settings()
    a = Auth(PostgresAuth(s), RedisLimiter(s))
    return reset_password(a, args.email)


if __name__ == "__main__":
    sys.exit(main())
