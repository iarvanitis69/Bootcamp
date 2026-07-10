# Terminal Management System

Flask API for the Mellon Group DevOps Bootcamp final assignment. The local stack runs with Docker Compose and includes:

- `tms-api`: Flask application built from `app/Dockerfile`
- `mysql`: official MySQL image with persistent data and automatic schema/seed import
- `redis`: official Redis image used as the cache layer

## Project Layout

```text
.
├── app/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
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

Application logs are written to stdout with timestamp, level, and message.

## Database Initialization

The SQL files in `db/init/` are mounted to `/docker-entrypoint-initdb.d` inside the MySQL container. The official MySQL image executes them automatically only when the database volume is created for the first time.

To recreate the database from scratch:

```bash
docker compose down -v
docker compose up --build
```

## Schema Note

The `mid` field belongs to the `merchants` table. The `terminals` table stores `merchant_id`, which references `merchants.id`; therefore a terminal's MID is retrieved through a join.


## data schema
mysql -u tms_app -p tms
Enter password: 
Reading table information for completion of table and column names
You can turn off this feature to get a quicker startup with -A

Welcome to the MySQL monitor.  Commands end with ; or \g.
Your MySQL connection id is 72
Server version: 8.4.10 MySQL Community Server - GPL

Copyright (c) 2000, 2026, Oracle and/or its affiliates.

Oracle is a registered trademark of Oracle Corporation and/or its
affiliates. Other names may be trademarks of their respective
owners.

Type 'help;' or '\h' for help. Type '\c' to clear the current input statement.

mysql> SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
    -> FROM INFORMATION_SCHEMA.COLUMNS
    -> WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'terminals'
    -> ORDER BY ORDINAL_POSITION;
+------------------+-----------+-------------+
| COLUMN_NAME      | DATA_TYPE | IS_NULLABLE |
+------------------+-----------+-------------+
| id               | int       | NO          |
| tid              | varchar   | NO          |
| merchant_id      | int       | NO          |
| template_id      | int       | YES         |
| serial_number    | varchar   | YES         |
| software_version | varchar   | YES         |
| sdk_version      | varchar   | YES         |
| scenario_number  | varchar   | YES         |
| hardware_model   | varchar   | YES         |
| hardware_family  | varchar   | YES         |
| enabled          | tinyint   | NO          |
| created_on       | datetime  | NO          |
| last_call_stamp  | datetime  | YES         |
+------------------+-----------+-------------+
13 rows in set (0.00 sec)
