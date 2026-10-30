#!/bin/sh
# Writes SeaweedFS's S3 identity from S3_ACCESS_KEY and S3_SECRET_KEY, so the secret lives only in
# .env, then starts the server as compose used to (ADR-0012).
set -eu
case "$S3_ACCESS_KEY$S3_SECRET_KEY" in
  *'"'* | *'\'*)
    echo 'S3_ACCESS_KEY and S3_SECRET_KEY must not contain a double quote or a backslash' >&2
    exit 1
    ;;
esac
cat > /tmp/s3.json <<JSON
{"identities": [{"name": "trayce",
  "credentials": [{"accessKey": "$S3_ACCESS_KEY", "secretKey": "$S3_SECRET_KEY"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON
exec /entrypoint.sh server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json
