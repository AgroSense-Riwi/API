"""Tests automatizados de verificación documental para SCRUM-47 (AGRO-28).

Verifica que los runbooks operacionales y escenarios de costo mínimo existan,
cumplan los criterios de aceptación E-15, RNF-04, CT-05 y definan las métricas
de RTO/RPO y el rango presupuestal ($5 - $48/mes).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"


def test_disaster_recovery_runbook_compliance() -> None:
    """SCRUM-189: Verifica existencia y contenido del Runbook de Recuperación ante Desastres."""
    dr_path = DOCS_RUNBOOKS_DIR / "DISASTER_RECOVERY_RUNBOOK.md"
    assert dr_path.is_file(), f"Falta runbook de desastres en {dr_path}"

    content = dr_path.read_text(encoding="utf-8")
    assert "RTO" in content
    assert "RPO" in content
    assert "pg_dump" in content
    assert "pg_restore" in content
    assert "RabbitMQ" in content
    assert "dead.letter.queue" in content


def test_minimum_viable_cost_scenario_compliance() -> None:
    """SCRUM-190: Verifica especificación del escenario de costo mínimo ($5 - $48/mes)."""
    cost_path = DOCS_RUNBOOKS_DIR / "MINIMUM_VIABLE_COST_SCENARIO.md"
    assert cost_path.is_file(), f"Falta documento de costos en {cost_path}"

    content = cost_path.read_text(encoding="utf-8")
    assert "$5" in content or "$5.00" in content
    assert "$48" in content or "$48.00" in content
    assert "Fargate Spot" in content
    assert "S3 Gateway" in content
    assert "Snappy" in content


def test_system_maintenance_procedures_compliance() -> None:
    """SCRUM-191: Verifica procedimientos de arranque, apagado y mantenimiento rutinario."""
    maint_path = DOCS_RUNBOOKS_DIR / "SYSTEM_MAINTENANCE_PROCEDURES.md"
    assert maint_path.is_file(), f"Falta guía de mantenimiento en {maint_path}"

    content = maint_path.read_text(encoding="utf-8")
    assert "docker compose up" in content
    assert "alembic upgrade" in content
    assert "VACUUM" in content
    assert "CHECKPOINT" in content
    assert "rabbitmqctl" in content
