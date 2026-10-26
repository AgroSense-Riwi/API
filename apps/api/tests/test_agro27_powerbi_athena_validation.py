"""Tests automatizados de verificación para SCRUM-46 (AGRO-27):
Dashboard de analítica Power BI, conector Athena e integración end-to-end.

Cubre:
- SCRUM-186: Configuración del conector DirectQuery / Import vía Athena y PostgreSQL.
- SCRUM-187: Especificación de dashboards visuales de trazabilidad, riesgos agronómicos y huella hídrica.
- SCRUM-188: Visualizador cartográfico PostGIS y filtrado RLS de compradores.
- SCRUM-200: Configuración de AWS Glue Catalog y Athena sobre el bucket S3 Gold.
- SCRUM-201: Configuración de autenticación del conector ODBC de Athena en Power BI Desktop.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_ANALYTICS_DIR = REPO_ROOT / "docs" / "analytics"


def test_powerbi_athena_dashboard_guide_exists() -> None:
    """Verifica que la guía exhaustiva de Power BI y Athena exista en el repositorio."""
    doc_path = DOCS_ANALYTICS_DIR / "POWER_BI_ATHENA_DASHBOARDS.md"
    assert doc_path.is_file(), f"Falta documento en {doc_path}"

    content = doc_path.read_text(encoding="utf-8")

    # SCRUM-186: DirectQuery & Import
    assert "DirectQuery" in content
    assert "PostgreSQL" in content

    # SCRUM-187: Dashboards visuales
    assert "Executive Farm Traceability" in content
    assert "Agronomic Risk" in content
    assert "Water Footprint" in content

    # SCRUM-188: Mapa PostGIS y RLS
    assert "PostGIS" in content or "GeoJSON" in content
    assert "InternationalBuyerRole" in content or "USERNAME()" in content

    # SCRUM-200: AWS Glue & Athena DDL
    assert "AWS Glue Data Catalog" in content
    assert "agrosense_datalake" in content

    # SCRUM-201: Simba ODBC
    assert "Simba Amazon Athena ODBC Driver" in content
    assert "DSN" in content
