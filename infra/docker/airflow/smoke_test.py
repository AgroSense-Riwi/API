"""Smoke test de la imagen agrosense-airflow (lo ejecuta build-airflow-image.yml).

1. Los DAGs de /opt/airflow/dags se parsean sin errores de importación.
2. PySpark arranca una SparkSession local con el Java de la imagen.
"""

from __future__ import annotations

try:  # Airflow >= 3.1
    from airflow.dag_processing.dagbag import DagBag
except ImportError:  # pragma: no cover - Airflow 3.0
    from airflow.models.dagbag import DagBag

from gold_star_schema_pyspark import build_spark_session

EXPECTED_DAGS = {"dag_gold_star_schema", "dag_satellite_ingestion"}

bag = DagBag("/opt/airflow/dags", include_examples=False)
if bag.import_errors:
    raise SystemExit(f"Errores de importación: {bag.import_errors}")
missing = EXPECTED_DAGS - set(bag.dag_ids)
if missing:
    raise SystemExit(f"DAGs no encontrados: {sorted(missing)} (hay {sorted(bag.dag_ids)})")
print("DAGs:", sorted(bag.dag_ids))

spark = build_spark_session("agrosense-airflow-smoke")
if spark.range(10).count() != 10:
    raise SystemExit("SparkSession local no devolvió 10 filas")
print("PySpark", spark.version, "OK")
spark.stop()
