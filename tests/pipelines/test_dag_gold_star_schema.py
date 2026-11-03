"""Tests del DAG dag_gold_star_schema (SCRUM-184 / SCRUM-185)."""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DAGS_DIR = os.path.join(BASE_DIR, "pipelines", "airflow", "dags")
if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)

import dag_gold_star_schema as gold_dag  # noqa: E402


def _result(**counts: int) -> dict:
    base = {"dim_finca": 2, "dim_tiempo": 365, "fact_entregas": 3, "fact_alertas": 0}
    base.update(counts)
    return {
        "counts": base,
        "uploaded_s3_uris": {name: [f"s3://b/gold/{name}/part-0.parquet"] for name in base},
    }


def test_dag_configuration() -> None:
    dag = gold_dag.dag
    assert dag.dag_id == "dag_gold_star_schema"
    assert dag.schedule == "0 6 * * *"
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert dag.default_args["retries"] == 2
    assert dag.default_args["execution_timeout"] == timedelta(hours=1)
    task_ids = {t.task_id for t in dag.tasks}
    assert task_ids == {"build_gold_star_schema", "validate_gold_star_schema"}


def test_build_task_runs_pyspark_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    def fake_job(database_url: str, bucket: str) -> dict:
        calls.update(database_url=database_url, bucket=bucket)
        return _result()

    monkeypatch.setitem(
        sys.modules, "gold_star_schema_pyspark", SimpleNamespace(run_gold_star_schema_job=fake_job)
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/agrosense")
    monkeypatch.setenv("S3_BUCKET_GOLD", "datalakeagrosense")

    result = gold_dag.build_gold_star_schema.function()
    assert result["counts"]["dim_tiempo"] == 365
    assert calls == {
        "database_url": "postgresql+psycopg://u:p@db:5432/agrosense",
        "bucket": "datalakeagrosense",
    }


def test_build_task_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGROSENSE_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        gold_dag._database_url()


def test_validate_accepts_empty_facts() -> None:
    out = gold_dag.validate_gold_star_schema.function(_result(fact_entregas=0))
    assert out["status"] == "VALIDATED"


def test_validate_rejects_empty_dimensions() -> None:
    with pytest.raises(ValueError, match="dim_finca"):
        gold_dag.validate_gold_star_schema.function(_result(dim_finca=0))


def test_validate_rejects_missing_upload() -> None:
    result = _result()
    result["uploaded_s3_uris"]["fact_alertas"] = []
    with pytest.raises(ValueError, match="fact_alertas"):
        gold_dag.validate_gold_star_schema.function(result)
