"""Credenciales API de compradores internacionales (RNF-06 / RN-11)

Agrega ``api_key_hash`` (hash Argon2 del secreto del comprador) e ``is_active`` a
``international_buyers``. ``client_id`` ya existe como UNIQUE NOT NULL desde la revision
inicial. Ambas columnas nuevas son compatibles con el seed (nullable / server default).

Revision ID: a3f1c9d2e7b4
Revises: c1a8f93e2b45
Create Date: 2026-10-31 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f1c9d2e7b4"
down_revision: str | None = "c1a8f93e2b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "international_buyers",
        sa.Column("api_key_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "international_buyers",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("international_buyers", "is_active")
    op.drop_column("international_buyers", "api_key_hash")
