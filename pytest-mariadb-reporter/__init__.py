"""pytest-mariadb-reporter package entrypoint.

This package is exposed to pytest via the ``pytest11`` entry point so the
plugin is auto-discovered after installation without requiring a ``conftest.py``
registration step.
"""

from .plugin import pytest_addoption, pytest_configure

__version__ = "0.1.0"

__all__ = ["__version__", "pytest_addoption", "pytest_configure"]
