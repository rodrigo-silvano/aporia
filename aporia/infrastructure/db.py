"""
Database connection, statement wrapper and transaction management for APORIA.
Provides full PDO-like semantics over SQLite or any DB-API 2.0 driver.
"""

from __future__ import annotations
import sqlite3
from typing import Any, Sequence


class RowCount(int):
    """Integer that can also be called as a function (PDO rowCount() compatibility)."""
    def __call__(self) -> int:
        return int(self)


class Statement:
    """PDO-compatible Statement wrapper."""

    def __init__(self, connection: Connection, sql: str) -> None:
        self.connection = connection
        self.sql = sql
        self.cursor: sqlite3.Cursor | None = None

    def execute(self, params: Sequence[Any] | None = None) -> Statement:
        self.cursor = self.connection.raw_connection.cursor()
        self.cursor.execute(self.sql, tuple(params or ()))
        return self

    def fetch(self) -> dict[str, Any] | None:
        if self.cursor is None:
            return None
        row = self.cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchAll(self) -> list[dict[str, Any]]:
        if self.cursor is None:
            return []
        return [dict(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        if self.cursor is None:
            return iter(())
        return (dict(row) for row in self.cursor)

    def fetch_all(self) -> list[dict[str, Any]]:
        return self.fetchAll()

    def fetch_one(self) -> dict[str, Any] | None:
        return self.fetch()

    def fetchColumn(self, column: int = 0) -> Any:
        if self.cursor is None:
            return None
        row = self.cursor.fetchone()
        if row is None:
            return False
        return row[column]

    def fetch_column(self, column: int = 0) -> Any:
        return self.fetchColumn(column)

    @property
    def rowcount(self) -> RowCount:
        return RowCount(self.cursor.rowcount if self.cursor else 0)

    @property
    def rowCount(self) -> RowCount:
        return self.rowcount

    @property
    def row_count(self) -> RowCount:
        return self.rowcount


class Connection:
    """
    Database connection wrapper. Defaults to in-memory SQLite if no db_path is given.
    """

    def __init__(self, db_path: str = ":memory:", raw_conn: sqlite3.Connection | None = None) -> None:
        if raw_conn is not None:
            self.raw_connection = raw_conn
        else:
            self.raw_connection = sqlite3.connect(db_path, isolation_level=None)
        self.raw_connection.row_factory = sqlite3.Row
        self._in_transaction = False

    @classmethod
    def in_memory(cls) -> Connection:
        return cls(":memory:")

    def in_transaction(self) -> bool:
        return self._in_transaction

    def inTransaction(self) -> bool:
        return self.in_transaction()

    def begin_transaction(self) -> None:
        if not self._in_transaction:
            self.raw_connection.execute("BEGIN")
            self._in_transaction = True

    def beginTransaction(self) -> None:
        self.begin_transaction()

    def commit(self) -> None:
        if self._in_transaction:
            self.raw_connection.execute("COMMIT")
            self._in_transaction = False

    def roll_back(self) -> None:
        if self._in_transaction:
            self.raw_connection.execute("ROLLBACK")
            self._in_transaction = False

    def rollBack(self) -> None:
        self.roll_back()

    def prepare(self, sql: str) -> Statement:
        return Statement(self, sql)

    def exec(self, sql: str) -> int:
        cursor = self.raw_connection.cursor()
        cursor.execute(sql)
        return cursor.rowcount

    def executescript(self, sql: str) -> None:
        self.raw_connection.executescript(sql)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Statement:
        stmt = self.prepare(sql)
        stmt.execute(params)
        return stmt

    def query(self, sql: str, params: Sequence[Any] | None = None) -> Statement:
        return self.execute(sql, params)

    def last_insert_id(self) -> int:
        cursor = self.raw_connection.cursor()
        cursor.execute("SELECT last_insert_rowid()")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def lastInsertId(self) -> int:
        return self.last_insert_id()

    def getAttribute(self, attr: int) -> Any:
        return "sqlite"

    def close(self) -> None:
        self.raw_connection.close()


def get_connection(db_path: str | None = None) -> Connection:
    import os
    if db_path is None:
        db_path = os.getenv("APORIA_DB_PATH") or os.getenv("DB_DATABASE") or ":memory:"
    return Connection(db_path)
