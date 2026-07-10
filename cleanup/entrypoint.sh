#!/bin/sh
set -eu

: "${CLEANUP_CRON:=0 2 * * *}"

cat > /etc/tms-cleanup.env <<EOF
MYSQL_HOST='${MYSQL_HOST:-mysql}'
MYSQL_PORT='${MYSQL_PORT:-3306}'
MYSQL_DATABASE='${MYSQL_DATABASE:-tms}'
MYSQL_USER='${MYSQL_USER:-tms_app}'
MYSQL_PASSWORD='${MYSQL_PASSWORD:-}'
REDIS_HOST='${REDIS_HOST:-redis}'
REDIS_PORT='${REDIS_PORT:-6379}'
REDIS_DB='${REDIS_DB:-0}'
EOF

if [ -n "${CLEANUP_CRON:-}" ]; then
  printf '%s . /etc/tms-cleanup.env; /usr/local/bin/cleanup.sh >> /proc/1/fd/1 2>> /proc/1/fd/2\n' "$CLEANUP_CRON" > /etc/tms-cleanup.generated
else
  cp /etc/tms-cleanup.crontab /etc/tms-cleanup.generated
fi

echo "Installed crontab:"
cat /etc/tms-cleanup.generated

if [ "${CLEANUP_ONCE:-false}" = "true" ]; then
  exec /bin/sh -c ". /etc/tms-cleanup.env; /usr/local/bin/cleanup.sh"
fi

exec /usr/local/bin/supercronic /etc/tms-cleanup.generated
