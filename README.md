# Terminal Management System

Flask API for the Mellon Group DevOps Bootcamp final assignment. The local stack runs with Docker Compose and includes:

- `tms-api`: Flask application built from `app/Dockerfile`
- `mysql`: official MySQL image with persistent data and automatic schema/seed import
- `redis`: official Redis image used as the cache layer
- `tms-cleanup`: crontab-based cleanup worker for expired decommissioned terminals

The Flask entrypoint is `app/main.py`. The cleanup worker uses a crontab entry executed by `supercronic` inside a separate container.

## Project Layout

```text
.
├── app/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── cleanup/
│   ├── Dockerfile
│   ├── cleanup.sh
│   ├── crontab
│   └── entrypoint.sh
├── db/
│   └── init/
│       ├── 01_schema.sql
│       └── 02_seed.sql
├── docker-compose.yml
├── .env.example
└── README.md
```

## Environment

Create a local `.env` file from the template:

```bash
cp .env.example .env
```

Update the password values before running the stack. The real `.env` file is ignored by git.

## Run

```bash
docker compose up --build
```

The API will be available at:

```text
http://localhost:5000
```

Useful endpoints:

- `GET /health`: checks both MySQL and Redis connectivity and returns `503` if either component is degraded
- `GET /schema/terminals`: runs the schema exploration query from the assignment
- `GET /terminals`: lists all terminals
- `GET /terminals?enabled=true`: lists enabled terminals
- `GET /terminals?enabled=false`: lists decommissioned/disabled terminals
- `GET /templates`: lists templates
- `GET /templates/<id>`: returns one template
- `POST /terminals/from-template`: creates a terminal from a template and merchant MID
- `GET /terminals/<tid>`: returns details for one terminal
- `GET /terminals/flagged`: lists terminals with a non-zero scenario number
- `POST /terminals/<tid>/flag`: sets `scenario_number` and updates `updated_on`
- `POST /terminals/<tid>/unflag`: sets `scenario_number` to `0` and updates `updated_on`
- `POST /terminals/<tid>/decommission`: disables a terminal and queues it for deletion after 3 days
- `GET /terminals/decommissioned`: lists terminals in the decommission queue
- `GET /statistics/by-hardware`: terminal count by hardware model
- `GET /statistics/by-state`: active/inactive terminal counts
- `GET /statistics/by-hardware-family`: terminal count by hardware family
- `GET /statistics/idle-distribution`: terminal count by idle-days bucket

Application logs are written to stdout with timestamp, level, and message. Database and Redis operations are wrapped with explicit error handling and return JSON error responses instead of silent failures.

## Redis Caching

The API uses a cache-aside pattern with Redis. `GET /terminals` is cached for 30 seconds, and `GET /statistics/*` endpoints are cached for 60 seconds. Cache operations are best-effort: if Redis is unavailable, the API logs the Redis error and continues by reading from MySQL.

Every write endpoint clears the whole application cache before returning:

- `POST /terminals/<tid>/flag`
- `POST /terminals/<tid>/unflag`
- `POST /terminals/<tid>/decommission`
- `POST /terminals/from-template`

To verify cache behavior, watch the API logs:

```bash
docker compose logs -f tms-api
```

In another terminal:

```bash
curl http://localhost:5000/terminals
curl http://localhost:5000/terminals
curl -X POST http://localhost:5000/terminals/T0101001/flag \
  -H "Content-Type: application/json" \
  -d '{"scenario_number":"5"}'
curl http://localhost:5000/terminals
```

The expected log flow is `Cache MISS`, then `Cache HIT`, then cache clear after the write, then `Cache MISS` again.

## Database Initialization

The SQL files in `db/init/` are mounted to `/docker-entrypoint-initdb.d` inside the MySQL container. The official MySQL image executes them automatically only when the database volume is created for the first time.

To recreate the database from scratch:

```bash
docker compose down -v
docker compose up --build
```

## Schema Note

The `mid` field belongs to the `merchants` table. The `terminals` table stores `merchant_id`, which references `merchants.id`; therefore a terminal's MID is retrieved through a join.

On API startup, the app applies the Feature A schema additions idempotently:

- Adds `terminals.updated_on` only if the column does not already exist.
- Creates `decommission_queue` only if the table does not already exist.

## Feature A Examples

List terminals:

```bash
curl http://localhost:5000/terminals
curl "http://localhost:5000/terminals?enabled=true"
```

Terminal details:

```bash
curl http://localhost:5000/terminals/T0101001
```

Flag and unflag:

```bash
curl -X POST http://localhost:5000/terminals/T0101001/flag \
  -H "Content-Type: application/json" \
  -d '{"scenario_number":"5"}'

curl -X POST http://localhost:5000/terminals/T0101001/unflag
```

Decommission:

```bash
curl -X POST http://localhost:5000/terminals/T0101001/decommission
curl http://localhost:5000/terminals/decommissioned
```

## Feature B Examples

List templates:

```bash
curl http://localhost:5000/templates
```

Template details:

```bash
curl http://localhost:5000/templates/1
```

Create terminal from template:

```bash
curl -X POST http://localhost:5000/terminals/from-template \
  -H "Content-Type: application/json" \
  -d '{"template_id":1,"mid":"MID000101"}'
```

The create operation runs in a single database transaction. It validates the template, validates the merchant MID, locks the merchant's existing terminals, calculates the next TID suffix, inserts the new terminal, and returns `201 Created`.

## Feature D Examples

Statistics endpoints use Pandas for aggregation and Redis cache with a 60-second TTL:

```bash
curl http://localhost:5000/statistics/by-hardware
curl http://localhost:5000/statistics/by-state
curl http://localhost:5000/statistics/by-hardware-family
curl http://localhost:5000/statistics/idle-distribution
```

## Tests

Run the unit tests with:

```bash
pytest -q
```

The tests use fake database and Redis objects, so they do not require Docker containers to be running.

## Bonus Cron Cleanup

The `tms-cleanup` service runs in a separate container with a crontab file. Configure the schedule with:

```dotenv
CLEANUP_CRON=0 2 * * *
```

At container startup, `cleanup/entrypoint.sh` writes this crontab line to `/etc/tms-cleanup.generated`:

```cron
0 2 * * * . /etc/tms-cleanup.env; /usr/local/bin/cleanup.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
```

The shell script deletes expired rows where `decommission_queue.delete_after < NOW()`. Because `decommission_queue.tid` has a foreign key to `terminals.tid`, it deletes in this order inside one transaction:

1. Delete from `decommission_queue`.
2. Delete the matching rows from `terminals`.

Run the cleanup once manually for verification:

```bash
docker compose run --rm -e CLEANUP_ONCE=true tms-cleanup
```

Inspect the installed crontab file:

```bash
docker exec tms-cleanup cat /etc/tms-cleanup.generated
```

Watch cron runner logs:

```bash
docker compose logs -f tms-cleanup
```

### Cleanup File Details

`cleanup/Dockerfile`

Builds the image for the cleanup container. It uses the official `mysql:8.4` image so the container has the correct MySQL 8 client and can authenticate against the MySQL server. It downloads `supercronic`, copies the cleanup scripts into the image, and sets `cleanup/entrypoint.sh` as the container entrypoint.

`cleanup/entrypoint.sh`

Runs when the `tms-cleanup` container starts. It creates `/etc/tms-cleanup.env` from Docker environment variables, creates `/etc/tms-cleanup.generated` with the final crontab line, prints that crontab line to stdout, and starts `supercronic`. If `CLEANUP_ONCE=true` is passed, it runs `cleanup.sh` once and exits instead of starting the cron runner.

`cleanup/crontab`

Contains the default crontab line:

```cron
0 2 * * * . /etc/tms-cleanup.env; /usr/local/bin/cleanup.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
```

`cleanup/cleanup.sh`

Does the actual cleanup work. It validates required MySQL variables, selects expired TIDs from `decommission_queue`, deletes those rows from `decommission_queue`, then deletes the matching terminals from `terminals`. The delete order matters because `decommission_queue.tid` references `terminals.tid` with a foreign key. The script also clears Redis `tms:*` keys after a successful cleanup.

### Crontab Details

The default schedule is:

```cron
0 2 * * *
```

Crontab fields are:

```text
minute hour day-of-month month day-of-week
```

So `0 2 * * *` means:

- `0`: at minute 0
- `2`: at hour 2, meaning 02:00
- `*`: every day of the month
- `*`: every month
- `*`: every day of the week

Therefore the cleanup runs every day at 02:00 inside the cleanup container.

The full generated crontab command is:

```cron
0 2 * * * . /etc/tms-cleanup.env; /usr/local/bin/cleanup.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
```

This does three things:

1. `. /etc/tms-cleanup.env` loads the MySQL and Redis environment variables for the cron process.
2. `/usr/local/bin/cleanup.sh` runs the cleanup script.
3. `>> /proc/1/fd/1 2>> /proc/1/fd/2` sends stdout and stderr to the container logs, so `docker compose logs tms-cleanup` shows cron output.

The generated environment file contains values like:

```sh
MYSQL_HOST='mysql'
MYSQL_PORT='3306'
MYSQL_DATABASE='tms'
MYSQL_USER='tms_app'
MYSQL_PASSWORD='...'
REDIS_HOST='redis'
REDIS_PORT='6379'
REDIS_DB='0'
```

Inspect the generated files inside the running container:

```bash
docker exec tms-cleanup cat /etc/tms-cleanup.generated
docker exec tms-cleanup cat /etc/tms-cleanup.env
```


## Data Schema Check

Use this query to inspect the `terminals` table:

```sql
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'terminals'
ORDER BY ORDINAL_POSITION;
```

After the API starts, the result includes the assignment seed columns plus the `updated_on` column added by the application migration.
