FROM mysql:8.4

ADD https://github.com/aptible/supercronic/releases/download/v0.2.34/supercronic-linux-amd64 /usr/local/bin/supercronic

COPY cleanup.sh /usr/local/bin/cleanup.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY crontab /etc/tms-cleanup.crontab

RUN chmod +x /usr/local/bin/supercronic /usr/local/bin/cleanup.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
