import logging
import json
import os
import sys
from datetime import date, datetime
from typing import Any

import redis
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

load_dotenv()

db = SQLAlchemy()
logger = logging.getLogger(__name__)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: serialize_value(value) for key, value in row.items()}


def bool_value(value: Any) -> bool:
    return bool(int(value))


def terminal_list_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tid": row["tid"],
        "mid": row["mid"],
        "hardware_model": row["hardware_model"],
        "software_version": row["software_version"],
        "enabled": bool_value(row["enabled"]),
        "last_call": serialize_value(row["last_call_stamp"]),
    }


def generated_at() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def cache_key(name: str) -> str:
    return f"tms:{name}"


def get_cached_json(redis_client: redis.Redis, key: str) -> Any | None:
    try:
        cached = redis_client.get(key)
        if cached is None:
            logger.info("Cache MISS %s", key)
            return None
        logger.info("Cache HIT %s", key)
        return json.loads(cached)
    except Exception:
        logger.error("Redis cache read failed", exc_info=True)
        return None


def set_cached_json(redis_client: redis.Redis, key: str, value: Any, ttl: int = 60) -> None:
    try:
        redis_client.setex(key, ttl, json.dumps(value))
    except Exception:
        logger.error("Redis cache write failed", exc_info=True)


def clear_cache(redis_client: redis.Redis) -> None:
    try:
        keys = list(redis_client.scan_iter(match="tms:*"))
        if keys:
            redis_client.delete(*keys)
            logger.info("Cleared %s cache keys", len(keys))
    except Exception:
        logger.error("Redis cache clear failed", exc_info=True)


def dataframe_from_query(query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    result = db.session.execute(text(query), params or {})
    return pd.DataFrame(result.mappings().all())


def ensure_schema() -> None:
    updated_on_exists = db.session.execute(
        text(
            """
            SELECT COUNT(*) AS count
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'terminals'
              AND COLUMN_NAME = 'updated_on'
            """
        )
    ).scalar()

    if not updated_on_exists:
        db.session.execute(text("ALTER TABLE terminals ADD COLUMN updated_on DATETIME NULL"))
        logger.info("Added terminals.updated_on column")

    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS decommission_queue (
                tid VARCHAR(20) PRIMARY KEY,
                queued_on DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                delete_after DATETIME NOT NULL,
                CONSTRAINT fk_decommission_queue_terminal
                    FOREIGN KEY (tid) REFERENCES terminals(tid)
            )
            """
        )
    )
    db.session.commit()


def create_app() -> Flask:
    app = Flask(__name__)

    mysql_user = required_env("MYSQL_USER")
    mysql_password = required_env("MYSQL_PASSWORD")
    mysql_host = os.getenv("MYSQL_HOST", "mysql")
    mysql_port = os.getenv("MYSQL_PORT", "3306")
    mysql_database = required_env("MYSQL_DATABASE")

    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{mysql_user}:{mysql_password}"
        f"@{mysql_host}:{mysql_port}/{mysql_database}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )

    with app.app_context():
        try:
            ensure_schema()
        except Exception:
            logger.error("Database schema initialization failed", exc_info=True)
            raise

    @app.get("/health")
    def health():
        checks = {"database": "ok", "redis": "ok"}
        status_code = 200

        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            logger.error("Database health check failed", exc_info=True)
            checks["database"] = "degraded"
            status_code = 503

        try:
            redis_client.ping()
        except Exception:
            logger.error("Redis health check failed", exc_info=True)
            checks["redis"] = "degraded"
            status_code = 503

        return jsonify(status="ok" if status_code == 200 else "degraded", checks=checks), status_code

    @app.get("/schema/terminals")
    def terminal_columns():
        try:
            rows = db.session.execute(
                text(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'terminals'
                    ORDER BY ORDINAL_POSITION
                    """
                )
            ).mappings()
            return jsonify([dict(row) for row in rows])
        except Exception:
            logger.error("Database schema query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/templates")
    def list_templates():
        key = cache_key("templates:list")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            rows = db.session.execute(
                text(
                    """
                    SELECT id, template_name, hardware_model, hardware_family
                    FROM templates
                    ORDER BY id
                    """
                )
            ).mappings()
            result = [serialize_row(dict(row)) for row in rows]
            set_cached_json(redis_client, key, result)
            return jsonify(result)
        except Exception:
            logger.error("Database template list query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/templates/<int:template_id>")
    def template_details(template_id: int):
        key = cache_key(f"templates:detail:{template_id}")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            row = db.session.execute(
                text(
                    """
                    SELECT id, template_name, hardware_model, hardware_family
                    FROM templates
                    WHERE id = :template_id
                    """
                ),
                {"template_id": template_id},
            ).mappings().first()
            if row is None:
                return jsonify(error="template not found"), 404

            result = serialize_row(dict(row))
            set_cached_json(redis_client, key, result)
            return jsonify(result)
        except Exception:
            logger.error("Database template detail query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/terminals")
    def list_terminals():
        enabled = request.args.get("enabled")
        if enabled not in (None, "true", "false"):
            return jsonify(error="enabled must be true or false"), 400

        key = cache_key(f"terminals:list:enabled:{enabled}")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        params = {}
        query = """
            SELECT
                t.tid,
                m.mid,
                t.hardware_model,
                t.software_version,
                t.enabled,
                t.last_call_stamp
            FROM terminals t
            JOIN merchants m ON m.id = t.merchant_id
            ORDER BY t.tid
        """
        if enabled is not None:
            params["enabled"] = 1 if enabled == "true" else 0
            query = """
                SELECT
                    t.tid,
                    m.mid,
                    t.hardware_model,
                    t.software_version,
                    t.enabled,
                    t.last_call_stamp
                FROM terminals t
                JOIN merchants m ON m.id = t.merchant_id
                WHERE t.enabled = :enabled
                ORDER BY t.tid
            """

        try:
            rows = db.session.execute(
                text(query),
                params,
            ).mappings()
            result = [terminal_list_row(dict(row)) for row in rows]
            set_cached_json(redis_client, key, result, ttl=30)
            return jsonify(result)
        except Exception:
            logger.error("Database terminal list query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/terminals/flagged")
    def flagged_terminals():
        key = cache_key("terminals:flagged")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            rows = db.session.execute(
                text(
                    """
                    SELECT
                        t.tid,
                        m.mid,
                        t.hardware_model,
                        t.software_version,
                        t.enabled,
                        t.last_call_stamp,
                        t.scenario_number
                    FROM terminals t
                    JOIN merchants m ON m.id = t.merchant_id
                    WHERE t.scenario_number IS NOT NULL
                      AND t.scenario_number != ''
                      AND t.scenario_number != '0'
                    ORDER BY t.tid
                    """
                )
            ).mappings()
            result = [serialize_row(dict(row)) for row in rows]
            for item in result:
                item["enabled"] = bool_value(item["enabled"])
                item["last_call"] = item.pop("last_call_stamp")
            set_cached_json(redis_client, key, result)
            return jsonify(result)
        except Exception:
            logger.error("Database flagged terminals query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/terminals/decommissioned")
    def decommissioned_terminals():
        key = cache_key("terminals:decommissioned")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            rows = db.session.execute(
                text(
                    """
                    SELECT
                        q.tid,
                        q.queued_on,
                        q.delete_after,
                        GREATEST(
                            CEIL(TIMESTAMPDIFF(SECOND, NOW(), q.delete_after) / 86400),
                            0
                        ) AS days_remaining
                    FROM decommission_queue q
                    ORDER BY q.queued_on DESC
                    """
                )
            ).mappings()
            result = [serialize_row(dict(row)) for row in rows]
            for item in result:
                item["days_remaining"] = int(item["days_remaining"])
            set_cached_json(redis_client, key, result)
            return jsonify(result)
        except Exception:
            logger.error("Database decommission queue query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/statistics/by-hardware")
    def statistics_by_hardware():
        key = cache_key("statistics:by-hardware")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            df = dataframe_from_query("SELECT hardware_model FROM terminals")
            if df.empty:
                data = []
            else:
                grouped = (
                    df.fillna({"hardware_model": "Unknown"})
                    .groupby("hardware_model")
                    .size()
                    .reset_index(name="count")
                    .sort_values(["count", "hardware_model"], ascending=[False, True])
                )
                data = grouped.to_dict(orient="records")

            result = {"generated_at": generated_at(), "data": data}
            set_cached_json(redis_client, key, result, ttl=60)
            return jsonify(result)
        except Exception:
            logger.error("Database statistics by hardware query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/statistics/by-state")
    def statistics_by_state():
        key = cache_key("statistics:by-state")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            df = dataframe_from_query("SELECT enabled FROM terminals")
            if df.empty:
                active = inactive = total = 0
            else:
                df["enabled"] = df["enabled"].astype(int)
                active = int((df["enabled"] == 1).sum())
                inactive = int((df["enabled"] == 0).sum())
                total = int(len(df))

            result = {
                "generated_at": generated_at(),
                "active": active,
                "inactive": inactive,
                "total": total,
            }
            set_cached_json(redis_client, key, result, ttl=60)
            return jsonify(result)
        except Exception:
            logger.error("Database statistics by state query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/statistics/by-hardware-family")
    def statistics_by_hardware_family():
        key = cache_key("statistics:by-hardware-family")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            df = dataframe_from_query("SELECT hardware_family FROM terminals")
            if df.empty:
                data = []
            else:
                grouped = (
                    df.fillna({"hardware_family": "Unknown"})
                    .groupby("hardware_family")
                    .size()
                    .reset_index(name="count")
                    .sort_values(["count", "hardware_family"], ascending=[False, True])
                )
                data = grouped.to_dict(orient="records")

            result = {"generated_at": generated_at(), "data": data}
            set_cached_json(redis_client, key, result, ttl=60)
            return jsonify(result)
        except Exception:
            logger.error("Database statistics by hardware family query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/statistics/idle-distribution")
    def statistics_idle_distribution():
        key = cache_key("statistics:idle-distribution")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            df = dataframe_from_query("SELECT last_call_stamp FROM terminals")
            bucket_order = ["Σήμερα", "1-7 μέρες", "8-30 μέρες", "31-90 μέρες", "90+ μέρες"]
            if df.empty:
                data = [{"range": bucket, "count": 0} for bucket in bucket_order]
            else:
                last_call = pd.to_datetime(df["last_call_stamp"], errors="coerce")
                today = pd.Timestamp.now().normalize()
                idle_days = (today - last_call.dt.normalize()).dt.days.fillna(91)
                df["idle_bucket"] = pd.cut(
                    idle_days,
                    bins=[-1, 0, 7, 30, 90, float("inf")],
                    labels=bucket_order,
                    include_lowest=True,
                )
                counts = df["idle_bucket"].value_counts(sort=False)
                data = [
                    {"range": bucket, "count": int(counts.get(bucket, 0))}
                    for bucket in bucket_order
                ]

            result = {"generated_at": generated_at(), "data": data}
            set_cached_json(redis_client, key, result, ttl=60)
            return jsonify(result)
        except Exception:
            logger.error("Database statistics idle distribution query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.post("/terminals/from-template")
    def create_terminal_from_template():
        payload = request.get_json(silent=True) or {}
        if "template_id" not in payload:
            return jsonify(error="template_id is required"), 400
        if "mid" not in payload:
            return jsonify(error="mid is required"), 400

        try:
            template_id = int(payload["template_id"])
        except (TypeError, ValueError):
            return jsonify(error="template_id must be an integer"), 400

        mid = str(payload["mid"])

        try:
            template_row = db.session.execute(
                text(
                    """
                    SELECT id, hardware_model, hardware_family
                    FROM templates
                    WHERE id = :template_id
                    """
                ),
                {"template_id": template_id},
            ).mappings().first()
            if template_row is None:
                db.session.rollback()
                return jsonify(error="template not found"), 404

            merchant_row = db.session.execute(
                text(
                    """
                    SELECT id, mid
                    FROM merchants
                    WHERE mid = :mid
                    """
                ),
                {"mid": mid},
            ).mappings().first()
            if merchant_row is None:
                db.session.rollback()
                return jsonify(error="merchant not found"), 404

            terminals = db.session.execute(
                text(
                    """
                    SELECT tid
                    FROM terminals
                    WHERE merchant_id = :merchant_id
                    FOR UPDATE
                    """
                ),
                {"merchant_id": merchant_row["id"]},
            ).scalars().all()

            if not terminals:
                db.session.rollback()
                return jsonify(error="merchant has no terminal prefix to extend"), 400

            prefix = terminals[0][:-3]
            max_suffix = 0
            for terminal_tid in terminals:
                if not terminal_tid.startswith(prefix):
                    db.session.rollback()
                    return jsonify(error="merchant terminal prefix is inconsistent"), 500
                try:
                    suffix = int(terminal_tid[-3:])
                except ValueError:
                    db.session.rollback()
                    return jsonify(error="terminal suffix is invalid"), 500
                max_suffix = max(max_suffix, suffix)

            new_tid = f"{prefix}{max_suffix + 1:03d}"
            db.session.execute(
                text(
                    """
                    INSERT INTO terminals (
                        tid,
                        merchant_id,
                        template_id,
                        hardware_model,
                        hardware_family,
                        enabled,
                        scenario_number,
                        created_on,
                        updated_on
                    )
                    VALUES (
                        :tid,
                        :merchant_id,
                        :template_id,
                        :hardware_model,
                        :hardware_family,
                        1,
                        '0',
                        NOW(),
                        NOW()
                    )
                    """
                ),
                {
                    "tid": new_tid,
                    "merchant_id": merchant_row["id"],
                    "template_id": template_row["id"],
                    "hardware_model": template_row["hardware_model"],
                    "hardware_family": template_row["hardware_family"],
                },
            )
            db.session.commit()
            logger.info("Created terminal %s from template %s for MID %s", new_tid, template_id, mid)
            clear_cache(redis_client)
            return jsonify(tid=new_tid), 201
        except Exception:
            db.session.rollback()
            logger.error("Database create terminal from template failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/terminals/<tid>")
    def terminal_details(tid: str):
        key = cache_key(f"terminals:detail:{tid}")
        cached = get_cached_json(redis_client, key)
        if cached is not None:
            return jsonify(cached)

        try:
            row = db.session.execute(
                text(
                    """
                    SELECT
                        t.id,
                        t.tid,
                        t.merchant_id,
                        m.mid,
                        m.name AS merchant_name,
                        t.template_id,
                        tp.template_name,
                        t.serial_number,
                        t.software_version,
                        t.sdk_version,
                        t.scenario_number,
                        t.hardware_model,
                        t.hardware_family,
                        t.enabled,
                        t.created_on,
                        t.updated_on,
                        t.last_call_stamp
                    FROM terminals t
                    JOIN merchants m ON m.id = t.merchant_id
                    LEFT JOIN templates tp ON tp.id = t.template_id
                    WHERE t.tid = :tid
                    """
                ),
                {"tid": tid},
            ).mappings().first()
            if row is None:
                return jsonify(error="terminal not found"), 404

            result = serialize_row(dict(row))
            result["enabled"] = bool_value(result["enabled"])
            result["last_call"] = result.pop("last_call_stamp")
            set_cached_json(redis_client, key, result)
            return jsonify(result)
        except Exception:
            logger.error("Database terminal detail query failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.post("/terminals/<tid>/flag")
    def flag_terminal(tid: str):
        payload = request.get_json(silent=True) or {}
        if "scenario_number" not in payload:
            return jsonify(error="scenario_number is required"), 400

        new_scenario = str(payload["scenario_number"])

        try:
            row = db.session.execute(
                text("SELECT scenario_number FROM terminals WHERE tid = :tid FOR UPDATE"),
                {"tid": tid},
            ).mappings().first()
            if row is None:
                db.session.rollback()
                return jsonify(error="terminal not found"), 404

            old_scenario = row["scenario_number"]
            db.session.execute(
                text(
                    """
                    UPDATE terminals
                    SET scenario_number = :scenario_number,
                        updated_on = NOW()
                    WHERE tid = :tid
                    """
                ),
                {"tid": tid, "scenario_number": new_scenario},
            )
            db.session.commit()
            logger.info("Flagged terminal %s from %s to %s", tid, old_scenario, new_scenario)
            clear_cache(redis_client)
            return jsonify(tid=tid, scenario_number=new_scenario)
        except Exception:
            db.session.rollback()
            logger.error("Database terminal flag update failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.post("/terminals/<tid>/unflag")
    def unflag_terminal(tid: str):
        try:
            row = db.session.execute(
                text("SELECT scenario_number FROM terminals WHERE tid = :tid FOR UPDATE"),
                {"tid": tid},
            ).mappings().first()
            if row is None:
                db.session.rollback()
                return jsonify(error="terminal not found"), 404

            old_scenario = row["scenario_number"]
            db.session.execute(
                text(
                    """
                    UPDATE terminals
                    SET scenario_number = '0',
                        updated_on = NOW()
                    WHERE tid = :tid
                    """
                ),
                {"tid": tid},
            )
            db.session.commit()
            logger.info("Unflagged terminal %s from %s to 0", tid, old_scenario)
            clear_cache(redis_client)
            return jsonify(tid=tid, scenario_number="0")
        except Exception:
            db.session.rollback()
            logger.error("Database terminal unflag update failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.post("/terminals/<tid>/decommission")
    def decommission_terminal(tid: str):
        try:
            row = db.session.execute(
                text("SELECT enabled FROM terminals WHERE tid = :tid FOR UPDATE"),
                {"tid": tid},
            ).mappings().first()
            if row is None:
                db.session.rollback()
                return jsonify(error="terminal not found"), 404
            if not bool_value(row["enabled"]):
                db.session.rollback()
                return jsonify(error="terminal already decommissioned"), 409

            db.session.execute(
                text(
                    """
                    UPDATE terminals
                    SET enabled = 0,
                        updated_on = NOW()
                    WHERE tid = :tid
                    """
                ),
                {"tid": tid},
            )
            db.session.execute(
                text(
                    """
                    INSERT INTO decommission_queue (tid, queued_on, delete_after)
                    VALUES (:tid, NOW(), DATE_ADD(NOW(), INTERVAL 3 DAY))
                    ON DUPLICATE KEY UPDATE
                        queued_on = VALUES(queued_on),
                        delete_after = VALUES(delete_after)
                    """
                ),
                {"tid": tid},
            )
            db.session.commit()
            logger.info("Decommissioned terminal %s", tid)
            clear_cache(redis_client)
            return jsonify(tid=tid, enabled=False)
        except Exception:
            db.session.rollback()
            logger.error("Database terminal decommission update failed", exc_info=True)
            return jsonify(error="database error"), 500

    @app.get("/")
    def index():
        return jsonify(
            service="Terminal Management System API",
            endpoints=[
                "/health",
                "/schema/terminals",
                "/terminals",
                "/templates",
                "/templates/<id>",
                "/terminals/from-template",
                "/terminals/<tid>",
                "/terminals/flagged",
                "/terminals/decommissioned",
                "/statistics/by-hardware",
                "/statistics/by-state",
                "/statistics/by-hardware-family",
                "/statistics/idle-distribution",
            ],
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "5000")),
    )
