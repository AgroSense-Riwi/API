"""Credenciales de login de usuarios internos (RBAC, data_structure.md tabla users)

Agrega ``email`` (UNIQUE, nullable), ``password_hash`` (Argon2, nullable) e ``is_active``
(server default true) a ``users``. Las columnas son nullable / con default para que el seed
(que sólo escribe user_id, full_name, role) siga funcionando.

Revision ID: c8e2a7d5f0b1
Revises: a3f1c9d2e7b4
Create Date: 2026-10-31 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a7d5f0b1"
down_revision: str | None = "a3f1c9d2e7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=150), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_unique_constraint(op.f("uq_users_email"), "users", ["email"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_users_email"), "users", type_="unique")
    op.drop_column("users", "is_active")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
