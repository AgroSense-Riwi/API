"""Airflow 3.3 SimpleAuthManager: login real y secretos propios (sin reutilizar los de la API)."""

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_airflow_requires_login() -> None:
    compose = _compose()
    airflow_services = [name for name in compose["services"] if name.startswith("airflow-")]
    assert airflow_services
    for name in airflow_services:
        env = compose["services"][name]["environment"]
        assert env["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS"] == "false", name
        assert env["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS"].endswith(":admin")
        assert env["AIRFLOW_ADMIN_PASSWORD"].startswith("${AIRFLOW_ADMIN_PASSWORD")
    entrypoint = " ".join(compose["services"]["airflow-webserver"]["entrypoint"])
    assert "SIMPLE_AUTH_MANAGER_PASSWORDS_FILE" in entrypoint
    assert "exec /entrypoint" in entrypoint


def test_ecs_airflow_uses_dedicated_secrets_and_login() -> None:
    task_def = json.loads(
        (REPO_ROOT / "infra" / "aws" / "ecs" / "task-definition-airflow.json").read_text("utf-8")
    )
    container = task_def["containerDefinitions"][0]
    env = {item["name"]: item["value"] for item in container["environment"]}
    assert env["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS"] == "false"

    secrets = {item["name"]: item["valueFrom"] for item in container["secrets"]}
    assert "airflow-fernet-key" in secrets["AIRFLOW__CORE__FERNET_KEY"]
    assert "airflow-api-secret" in secrets["AIRFLOW__WEBSERVER__SECRET_KEY"]
    assert "airflow-database-url" in secrets["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"]
    assert "airflow-admin-password" in secrets["AIRFLOW_ADMIN_PASSWORD"]
    for name, value in secrets.items():
        assert "jwt-secret" not in value
        # La base operacional solo se expone como DATABASE_URL (lectura del DAG gold);
        # los metadatos de Airflow usan su propia base (airflow-database-url).
        if name != "DATABASE_URL":
            assert "database-url-sync" not in value, name
    assert secrets["DATABASE_URL"].endswith("agrosense/prod/database-url-sync")
    assert container["command"] == ["standalone"]
    assert "exec /entrypoint" in " ".join(container["entryPoint"])
