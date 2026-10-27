"""The owner's web session from the cookie jar `agenttrace-verify up` writes, as a Cookie header."""


def cookie_header(jar_path: str) -> str:
    # curl's jar: tab-separated, the name and value last; `#HttpOnly_` prefixes httpOnly cookies.
    with open(jar_path) as jar:
        return "; ".join(
            f"{f[5]}={f[6]}"
            for line in jar
            if len(f := line.rstrip("\n").split("\t")) == 7 and not line.startswith("# ")
        )
