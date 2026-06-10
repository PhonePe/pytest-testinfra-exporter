"""Storage backend implementations for pytest-mariadb-reporter."""

from .mariadb import MariaDBBackend
from .postgres import PostgresBackend

__all__ = ["MariaDBBackend", "PostgresBackend"]