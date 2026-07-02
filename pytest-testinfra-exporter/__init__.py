# Copyright (c) 2026 Original Author(s), PhonePe India Pvt. Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""pytest-testinfra-exporter package entrypoint.

This package is exposed to pytest via the ``pytest11`` entry point so the
plugin is auto-discovered after installation without requiring a ``conftest.py``
registration step.
"""

from .plugin import pytest_addoption, pytest_configure

__version__ = "0.1.0"

__all__ = ["__version__", "pytest_addoption", "pytest_configure"]
