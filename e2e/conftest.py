"""pytest wiring for the E2E suite (helpers live in harness.py)."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from harness import StubHttp, pg_database_factory, pg_server  # noqa: E402,F401


@pytest.fixture
def stub_http() -> Iterator[Callable[..., StubHttp]]:
    created: list[StubHttp] = []

    def factory(routes: Callable[..., tuple[int, Any]]) -> StubHttp:
        stub = StubHttp(routes)
        created.append(stub)
        return stub

    yield factory
    for stub in created:
        stub.close()
