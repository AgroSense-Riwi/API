"""Fixtures compartidas para los tests de la API."""

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from agrosense_api.core.rate_limit import limiter
from agrosense_api.db.session import get_db
from agrosense_api.script import app


class _FakeDialect:
    name = "fake"


class _FakeBind:
    dialect = _FakeDialect()


class FakeSession:
    """Sesión mínima: ``scalar`` devuelve el objeto configurado y registra las consultas."""

    def __init__(self, scalar_result: Any = None) -> None:
        self.scalar_result = scalar_result
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return self.scalar_result

    def get_bind(self) -> _FakeBind:
        return _FakeBind()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """Evita que los límites por IP (p. ej. 5/min en /auth/token) se crucen entre tests."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def override_db() -> Iterator[Callable[[Any], FakeSession]]:
    """Sustituye ``get_db`` por una ``FakeSession`` que devuelve ``scalar_result``."""

    def _install(scalar_result: Any = None) -> FakeSession:
        session = FakeSession(scalar_result)

        async def _fake_get_db() -> AsyncIterator[FakeSession]:
            yield session

        app.dependency_overrides[get_db] = _fake_get_db
        return session

    yield _install
    app.dependency_overrides.pop(get_db, None)
