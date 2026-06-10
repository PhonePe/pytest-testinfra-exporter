"""pytest-mariadb-reporter package entrypoint.

This package exposes pytest plugin hooks from :mod:`plugin`.
The storage implementation is selected at runtime using
``--report-backend`` (currently ``mariadb`` or ``postgres``).

The testinfra-specific parsing behavior is implemented in
:class:`plugin.TestinfraStorageReporter` and remains unchanged.
"""

from .plugin import pytest_addoption, pytest_configure

__all__ = ["pytest_addoption", "pytest_configure"]
