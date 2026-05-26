from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Sequence, Mapping
import logging

from psycopg2 import pool, sql
from psycopg2.extras import Json, execute_batch

from .config import settings

LOGGER = logging.getLogger(__name__)


@dataclass
class Database:
    minconn: int = 1
    maxconn: int = 5

    def __post_init__(self) -> None:
        self._pool = pool.SimpleConnectionPool(
            self.minconn,
            self.maxconn,
            dsn=settings.db_dsn,
        )
        if settings.db_auto_migrate:
            self.ensure_schema()

    @contextmanager
    def connection(self):  # type: ignore[override]
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        self._pool.closeall()

    def ensure_schema(self) -> None:
        table_ident = sql.Identifier(settings.db_schema, settings.db_table)
        ddl = sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id                  BIGSERIAL PRIMARY KEY,
                received_at         TIMESTAMPTZ NULL,
                bank_id             TEXT NULL,
                batch_id            TEXT NULL,
                source_app_id       TEXT NULL,
                payload_timestamp   TIMESTAMPTZ NULL,
                transaction_count   INTEGER NULL,
                transaction_id      TEXT NULL,
                tran_refno          TEXT NULL,
                src_account_number  TEXT NULL,
                amount              NUMERIC NULL,
                balance_available   NUMERIC NULL,
                trans_type          TEXT NULL,
                notice_datetime     TIMESTAMPTZ NULL,
                trans_time          TEXT NULL,
                trans_desc          TEXT NULL,
                ofs_account_number  TEXT NULL,
                ofs_account_name    TEXT NULL,
                ofs_bank_id         TEXT NULL,
                ofs_bank_name       TEXT NULL,
                is_virtual_trans    TEXT NULL,
                virtual_acc         TEXT NULL,
                transaction_json    JSONB NOT NULL,
                inserted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        ).format(table=table_ident)

        indexes = [
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {table} (bank_id, transaction_id) "
                "WHERE transaction_id IS NOT NULL;"
            ).format(
                idx=sql.Identifier(f"{settings.db_table}_unique_tx_idx"),
                table=table_ident,
            ),
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {idx} ON {table} (batch_id);"
            ).format(
                idx=sql.Identifier(f"{settings.db_table}_batch_idx"),
                table=table_ident,
            ),
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {idx} ON {table} (bank_id, received_at DESC);"
            ).format(
                idx=sql.Identifier(f"{settings.db_table}_bank_time_idx"),
                table=table_ident,
            ),
        ]

        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
                for stmt in indexes:
                    cur.execute(stmt)
            conn.commit()
        LOGGER.info("Ensured table %s.%s", settings.db_schema, settings.db_table)

    def insert_transactions(self, rows: Sequence[Mapping[str, object]]) -> int:
        if not rows:
            return 0

        columns = list(rows[0].keys())
        col_ids = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
        placeholders = sql.SQL(", ").join(sql.Placeholder(c) for c in columns)

        # ON CONFLICT on unique index — skip duplicates silently
        stmt = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({vals}) "
            "ON CONFLICT (bank_id, transaction_id) WHERE transaction_id IS NOT NULL DO NOTHING"
        ).format(
            table=sql.Identifier(settings.db_schema, settings.db_table),
            cols=col_ids,
            vals=placeholders,
        )

        normalized = []
        for row in rows:
            r = dict(row)
            if r.get("transaction_json") is not None and not isinstance(r["transaction_json"], Json):
                r["transaction_json"] = Json(r["transaction_json"])
            normalized.append(r)

        with self.connection() as conn:
            try:
                with conn.cursor() as cur:
                    execute_batch(cur, stmt, normalized, page_size=200)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)
