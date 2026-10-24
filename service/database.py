"""SQLAlchemy lifecycle and SQLite-specific configuration."""

import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import URL, Connection, create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry
from sqlalchemy.schema import CreateIndex

from models import Base


class Database:
    """Read sessions and serialized write transactions share one connection pool."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create('sqlite+pysqlite', database=str(path)),
            connect_args={'timeout': 15},
            hide_parameters=True,
        )
        event.listen(self.engine, 'connect', _configure_sqlite)
        event.listen(self.engine, 'begin', _begin_sqlite_transaction)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.write_sessions = sessionmaker(
            self.engine.execution_options(sqlite_write=True), expire_on_commit=False
        )
        Base.metadata.create_all(self.engine)
        self._migrate_legacy_schema()

    def _migrate_legacy_schema(self) -> None:
        """Add missing legacy schema elements without rebuilding tables."""
        with self.engine.execution_options(sqlite_write=True).begin() as connection:
            columns = {
                column['name'] for column in inspect(connection).get_columns('students')
            }
            if 'event_id' not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE students ADD COLUMN event_id TEXT'
                )
            # create_all skips indexes on tables that already exist.
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:
                    connection.execute(CreateIndex(index, if_not_exists=True))

    def close(self) -> None:
        self.engine.dispose()


def _configure_sqlite(
    connection: sqlite3.Connection, record: ConnectionPoolEntry
) -> None:
    # SQLAlchemy owns BEGIN/COMMIT, including on Python's legacy sqlite3 driver.
    connection.isolation_level = None
    with closing(connection.cursor()) as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')


def _begin_sqlite_transaction(connection: Connection) -> None:
    # Lock before reading attempt state, across threads/processes.
    statement = (
        'BEGIN IMMEDIATE'
        if connection.get_execution_options().get('sqlite_write')
        else 'BEGIN'
    )
    connection.exec_driver_sql(statement)
