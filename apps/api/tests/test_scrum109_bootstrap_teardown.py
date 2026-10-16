from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "verify_bootstrap.sh"
README_FILE = REPO_ROOT / "README.md"


def test_verify_bootstrap_script_exists_and_has_valid_bash_syntax() -> None:
    """Valida que scripts/verify_bootstrap.sh exista, sea ejecutable,

    utilice 'set -euo pipefail' y tenga sintaxis Bash válida (CT-01).
    """
    assert BOOTSTRAP_SCRIPT.is_file(), (
        f"El script de verificación no existe en: {BOOTSTRAP_SCRIPT}"
    )

    # Verificar permisos de ejecución
    assert os.access(BOOTSTRAP_SCRIPT, os.X_OK), (
        "scripts/verify_bootstrap.sh debe tener permisos de ejecución (chmod +x)."
    )

    script_content = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in script_content, (
        "scripts/verify_bootstrap.sh debe incluir 'set -euo pipefail'."
    )
    assert "docker compose up -d --build" in script_content, (
        "scripts/verify_bootstrap.sh debe ejecutar 'docker compose up -d --build'."
    )
    assert "1800" in script_content, (
        "scripts/verify_bootstrap.sh debe validar el umbral SLA de 30 minutos (1800s)."
    )
    assert "/health" in script_content, (
        "scripts/verify_bootstrap.sh debe verificar el endpoint /health de la API."
    )

    # Validar sintaxis con bash -n
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Error de sintaxis en scripts/verify_bootstrap.sh:\n{result.stderr}"
    )


def test_readme_contains_ct01_bootstrap_and_e04_teardown_commands() -> None:
    """Valida que el README.md documente explícitamente los comandos de arranque (CT-01),

    monitoreo de salud y protocolo de teardown seguro (E-04).
    """
    assert README_FILE.is_file(), f"El archivo README.md no existe en: {README_FILE}"

    readme_content = README_FILE.read_text(encoding="utf-8")

    required_snippets = [
        "docker compose up",
        "docker compose ps",
        "docker compose logs -f api",
        "/health",
        "docker compose down",
        "docker compose down -v",
        "docker system prune -f",
    ]

    for snippet in required_snippets:
        assert snippet in readme_content, (
            f"El comando o referencia obligatoria '{snippet}' no se encontró en README.md."
        )
