"""DAG: Gold Star Schema for Power BI (SCRUM-184 / SCRUM-185, CT-07).

Daily at 06:00 UTC, after ``dag_satellite_ingestion`` (02:00-05:00 UTC):

1. ``build_gold_star_schema``: runs the PySpark job
   ``pipelines/spark_jobs/gold_star_schema_pyspark.py`` (Spark ``local[*]`` inside the
   Airflow task), which reads PostgreSQL and replaces
   ``s3://<bucket>/gold/{dim_finca,dim_tiempo,fact_entregas,fact_alertas}/``.
2. ``validate_gold_star_schema``: fails if ``dim_finca`` or ``dim_tiempo`` is empty or
   if a table has no Parquet file in S3.

The Athena tables over those prefixes are created once by
``infra/aws/setup-airflow.sh`` (``ATHENA_GOLD_STAR_SCHEMA_DDL``); new files are read
without re-registering them.

Environment: ``DATABASE_URL`` (or ``AGROSENSE_DATABASE_URL``) and ``S3_BUCKET_GOLD``
(default ``S3_BUCKET`` / ``datalakeagrosense``).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Image: /opt/airflow/{dags,spark_jobs}. Repository: pipelines/{airflow/dags,spark_jobs}.
for candidate in (
    os.path.abspath(os.path.join(CURRENT_DIR, "..", "spark_jobs")),
    os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "spark_jobs")),
):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

logger = logging.getLogger(__name__)

try:
    from airflow.sdk import dag, task

    HAS_AIRFLOW = True
except ImportError:  # Local tests / CI without Airflow installed.
    HAS_AIRFLOW = False

    def task(task_id: str | None = None, **_: Any) -> Callable[[Callable[..., Any]], Any]:  # type: ignore[no-redef]
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            func.task_id = task_id or func.__name__  # type: ignore[attr-defined]
            func.function = func  # type: ignore[attr-defined]  # same attribute as Airflow tasks
            return func

        return decorator

    def dag(**dag_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:  # type: ignore[no-redef]
        def decorator(dag_func: Callable[..., Any]) -> Callable[..., Any]:
            def build() -> Any:
                class _Dag:
                    pass

                inst = _Dag()
                for key, value in dag_kwargs.items():
                    setattr(inst, key, value)
                inst.tasks = [build_gold_star_schema, validate_gold_star_schema]  # type: ignore[attr-defined]
                return inst

            return build

        return decorator


DEFAULT_ARGS = {
    "owner": "lead_data_engineer",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(hours=1),
    "email_on_failure": False,
    "email_on_retry": False,
}

REQUIRED_NON_EMPTY = ("dim_finca", "dim_tiempo")


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("AGROSENSE_DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set for the gold star schema job")
    return url


def _bucket() -> str:
    return os.getenv("S3_BUCKET_GOLD") or os.getenv("S3_BUCKET") or "datalakeagrosense"


@task(task_id="build_gold_star_schema")
def build_gold_star_schema() -> dict[str, Any]:
    """Run the PySpark job and return the row counts and the uploaded S3 URIs."""
    from gold_star_schema_pyspark import run_gold_star_schema_job

    result = run_gold_star_schema_job(_database_url(), _bucket())
    logger.info("Gold star schema counts: %s", result["counts"])
    return result


@task(task_id="validate_gold_star_schema")
def validate_gold_star_schema(result: dict[str, Any]) -> dict[str, Any]:
    """Fail the run if the dimensions are empty or a table was not uploaded."""
    counts = result["counts"]
    empty = [name for name in REQUIRED_NON_EMPTY if counts.get(name, 0) == 0]
    if empty:
        raise ValueError(f"Gold tables without rows: {', '.join(empty)}")
    missing = [name for name, uris in result["uploaded_s3_uris"].items() if not uris]
    if missing:
        raise ValueError(f"Gold tables without Parquet files in S3: {', '.join(missing)}")
    if counts.get("fact_entregas", 0) == 0:
        logger.warning("fact_entregas is empty: PostgreSQL has no harvest_deliveries yet")
    return {"status": "VALIDATED", "counts": counts}


@dag(
    dag_id="dag_gold_star_schema",
    description="PySpark: PostgreSQL -> Gold Star Schema (Parquet) en S3 para Athena/Power BI",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["gold", "pyspark", "star_schema", "athena", "power_bi"],
    doc_md=__doc__,
)
def gold_star_schema_pipeline() -> None:
    validate_gold_star_schema(build_gold_star_schema())


dag = gold_star_schema_pipeline()
