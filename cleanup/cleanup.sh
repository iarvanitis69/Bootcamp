#!/bin/sh
set -eu

required() {
  name="$1"
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
}

required MYSQL_HOST
required MYSQL_PORT
required MYSQL_DATABASE
required MYSQL_USER
required MYSQL_PASSWORD

tmp_tids="$(mktemp)"
trap 'rm -f "$tmp_tids"' EXIT

mysql_base_args="
  --host=${MYSQL_HOST}
  --port=${MYSQL_PORT}
  --user=${MYSQL_USER}
  --password=${MYSQL_PASSWORD}
  --database=${MYSQL_DATABASE}
  --batch
  --skip-column-names
"

mysql $mysql_base_args <<'SQL' > "$tmp_tids"
SELECT tid
FROM decommission_queue
WHERE delete_after < NOW()
ORDER BY tid;
SQL

if [ ! -s "$tmp_tids" ]; then
  echo "$(date -Iseconds) INFO No expired decommissioned terminals to delete"
  exit 0
fi

tid_csv="$(awk '{ gsub(/'\''/, "'\'''\''"); printf "%s'\''%s'\''", sep, $0; sep="," }' "$tmp_tids")"

mysql $mysql_base_args <<SQL
START TRANSACTION;
DELETE FROM decommission_queue WHERE tid IN (${tid_csv});
DELETE FROM terminals WHERE tid IN (${tid_csv});
COMMIT;
SQL

deleted_count="$(wc -l < "$tmp_tids" | tr -d ' ')"
echo "$(date -Iseconds) INFO Deleted ${deleted_count} expired decommissioned terminals"

if command -v redis-cli >/dev/null 2>&1; then
  redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -n "${REDIS_DB:-0}" --scan --pattern 'tms:*' \
    | xargs -r redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -n "${REDIS_DB:-0}" del >/dev/null || true
fi
