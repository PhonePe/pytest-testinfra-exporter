"""SQLAlchemy engine construction with safe URL handling."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from .backend import resolve_datastore_options


DRIVERS = {
    "mariadb": "mysql+pymysql",
    "postgres": "postgresql+psycopg2",
}


def create_backend_url(config, backend_name: str) -> URL:
    """Build a SQLAlchemy URL without interpolating credentials."""

    options = resolve_datastore_options(config, backend_name)
    return URL.create(
        DRIVERS[backend_name],
        username=options.get("user"),
        password=options.get("password"),
        host=options.get("host"),
        port=options.get("port"),
        database=options.get("database"),
    )


def create_backend_engine(config, backend_name: str):
    """Create the selected backend's SQLAlchemy engine."""

    return create_engine(create_backend_url(config, backend_name), pool_pre_ping=True)


def redacted_url(url: URL) -> str:
    """Render a URL with its password hidden."""

    return url.render_as_string(hide_password=True)
