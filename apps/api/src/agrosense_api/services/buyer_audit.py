"""Registro de auditoría de consultas de compradores (``buyer_api_audit_logs``, CA-09)."""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.db.session import _is_postgres

logger = logging.getLogger(__name__)

# Códigos que la especificación (data_structure.md, tabla 8) pide auditar.
AUDITED_STATUS_CODES = frozenset({200, 403})


async def record_buyer_api_access(
    session: AsyncSession,
    *,
    batch_code: str,
    http_status_code: int,
    latency_ms: float,
    requested_at: datetime,
) -> None:
    """Inserta una fila de auditoría y confirma la transacción.

    Usa la función ``log_buyer_api_access`` (migración ``a9c4e6b2d8f1``), que toma el comprador
    de ``app.current_buyer_id`` y resuelve ``batch_id`` aunque el lote sea ajeno. Se hace
    ``commit`` porque la sesión del request se cierra con rollback y, en un 403, la fila se
    perdería. Si la auditoría falla, se registra el error y no se altera la respuesta.
    """
    if http_status_code not in AUDITED_STATUS_CODES or not _is_postgres(session):
        return
    try:
        await session.execute(
            select(
                func.log_buyer_api_access(
                    batch_code,
                    http_status_code,
                    Decimal(f"{latency_ms:.2f}"),
                    requested_at,
                )
            )
        )
        await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Unable to write buyer_api_audit_logs (batch_code=%s, status=%s)",
            batch_code,
            http_status_code,
        )
        await session.rollback()
