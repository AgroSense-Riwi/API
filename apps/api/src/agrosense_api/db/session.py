from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agrosense_api.core.config import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    pool_recycle=settings.database_pool_recycle_seconds,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Valor de app.current_role que habilita la política RLS de operador (ver migración
# b7d4e2f9a1c3). Sólo se fija tras autenticar un operador interno con su rol.
OPERATOR_RLS_ROLE = "operator"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


async def set_buyer_context(session: AsyncSession, buyer_id: UUID) -> None:
    """Fija ``app.current_buyer_id`` sólo para la transacción en curso (SET LOCAL).

    ``set_config(..., true)`` equivale a ``SET LOCAL`` y admite parámetros enlazados. La
    sesión abre la transacción en este execute y todas las consultas posteriores del request
    corren dentro de ella; al cerrarse la sesión (rollback) el valor desaparece y la conexión
    vuelve limpia al pool.
    """
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT set_config('app.current_buyer_id', :buyer_id, true)"),
        {"buyer_id": str(buyer_id)},
    )


async def set_operator_context(session: AsyncSession) -> None:
    """Activa la ruta RLS de operador interno para la transacción en curso."""
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT set_config('app.current_role', :role, true)"),
        {"role": OPERATOR_RLS_ROLE},
    )
