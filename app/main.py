import os
import logging
import sys

import redis
from dotenv import load_dotenv
from flask import Flask, jsonify
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
    )

    @app.get("/health")
    def health():
        checks = {"database": "ok", "redis": "ok"}
        status_code = 200

        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Database health check failed")
            checks["database"] = "degraded"
            status_code = 503

        try:
            redis_client.ping()
        except Exception:
            logger.exception("Redis health check failed")
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
            logger.exception("Database schema query failed")
            return jsonify(error="database error"), 500

    @app.get("/")
    def index():
        return jsonify(
            service="Terminal Management System API",
            endpoints=["/health", "/schema/terminals"],
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "5000")),
    )
