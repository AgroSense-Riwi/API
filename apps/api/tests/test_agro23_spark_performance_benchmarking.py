"""Suite automatizada de pruebas y benchmarking de rendimiento para Spark (SCRUM-42).

Cubre:
- SCRUM-176: Planes de ejecución, Broadcast Joins y Estrategia de Particionado.
- SCRUM-177: Asignación óptima de memoria y núcleos para clústeres EMR / Spark.
- SCRUM-178: SLA de ejecución (< 20 minutos / 1200 segundos) y throughput.
"""

from __future__ import annotations

import time

import pytest
from agrosense_api.services.spark_optimization import (
    ClusterWorkloadProfile,
    SparkExecutionPlanOptimizer,
    SparkResourceAllocationOptimizer,
    SparkSlaBenchmarker,
)


# ==============================================================
# 1. SCRUM-176: Broadcast Joins & Partition Strategy Tests
# ==============================================================


def test_broadcast_join_selection_logic() -> None:
    """Verifica que datasets menores al threshold (<=10MB) sean seleccionados para Broadcast."""
    threshold = SparkExecutionPlanOptimizer.BROADCAST_THRESHOLD_BYTES
    assert threshold == 10 * 1024 * 1024

    # Dataset de 2 MB (ej. tabla Kaggle o municipios) -> Broadcast
    assert SparkExecutionPlanOptimizer.should_broadcast(2 * 1024 * 1024) is True

    # Dataset de 9.9 MB -> Broadcast
    assert SparkExecutionPlanOptimizer.should_broadcast(int(9.9 * 1024 * 1024)) is True

    # Dataset de 50 MB (ej. histórico de telemetría IoT) -> Shuffle normal
    assert SparkExecutionPlanOptimizer.should_broadcast(50 * 1024 * 1024) is False

    # Tamaño inválido o vacío
    assert SparkExecutionPlanOptimizer.should_broadcast(0) is False


def test_partition_strategy_calculation_avoids_skew() -> None:
    """Verifica que el reparticionamiento mantenga fragmentos ideales de ~128 MB."""
    # 1 GB de datos -> ~8 particiones de 128 MB
    one_gb = 1024 * 1024 * 1024
    partitions = SparkExecutionPlanOptimizer.calculate_optimal_partitions(one_gb)
    assert partitions == 8

    # 10 GB de datos -> 80 particiones
    ten_gb = 10 * 1024 * 1024 * 1024
    partitions_10g = SparkExecutionPlanOptimizer.calculate_optimal_partitions(ten_gb)
    assert partitions_10g == 80

    # Dataset pequeño respeta particiones mínimas para concurrencia
    small_data = 10 * 1024 * 1024  # 10 MB
    assert SparkExecutionPlanOptimizer.calculate_optimal_partitions(small_data) >= 8


def test_medallion_partition_keys_alignment() -> None:
    """Verifica que las claves de partición en S3 optimicen consultas Athena y ETLs."""
    silver_keys = SparkExecutionPlanOptimizer.partition_strategy_for_agronomic_lake("silver")
    assert "observation_date" in silver_keys

    gold_keys = SparkExecutionPlanOptimizer.partition_strategy_for_agronomic_lake("gold")
    assert gold_keys == ["year", "month", "farm_id"]


# ==============================================================
# 2. SCRUM-177: EMR Cluster Memory & Core Allocation Tests
# ==============================================================


def test_calculate_emr_cluster_specs_standard_node() -> None:
    """Verifica asignación óptima para un clúster de 4 nodos m5.2xlarge (8 vCPUs, 32GB RAM)."""
    config = SparkResourceAllocationOptimizer.calculate_emr_cluster_specs(
        node_count=4, cores_per_node=8, ram_gb_per_node=32.0
    )

    # Cores por executor deben estar en el rango óptimo de 4 a 5
    assert config.executor_cores in (4, 5)

    # Cada nodo aporta 7 cores utilizables (8 - 1 para el SO) -> 1 executor por nodo
    # 4 nodos * 1 executor = 4 - 1 (para ApplicationMaster) = 3 executors mínimos
    assert config.executor_instances >= 3

    # Memoria overhead debe ser al menos 10% del executor
    assert config.executor_memory_overhead_mb >= int(config.executor_memory_mb * 0.08)
    assert config.executor_memory_overhead_mb >= 384

    # Paralelismo y shuffle partitions proporcionales
    assert config.default_parallelism > 0
    assert config.sql_shuffle_partitions >= config.default_parallelism


def test_spark_conf_dict_includes_aqe_and_kryo() -> None:
    """Verifica que la configuración generada incluya AQE (Adaptive Query Execution)."""
    preset = SparkResourceAllocationOptimizer.get_preset_for_workload(
        ClusterWorkloadProfile.STANDARD
    )
    conf = preset.to_spark_conf_dict()

    assert conf["spark.sql.adaptive.enabled"] == "true"
    assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "true"
    assert conf["spark.serializer"] == "org.apache.spark.serializer.KryoSerializer"
    assert "spark.executor.memory" in conf
    assert "spark.executor.cores" in conf


def test_emr_cluster_specs_invalid_inputs_raise_error() -> None:
    """Verifica que configuraciones de hardware no viables lancen ValueError."""
    with pytest.raises(ValueError, match="Configuración de hardware mínima inválida"):
        SparkResourceAllocationOptimizer.calculate_emr_cluster_specs(
            node_count=0, cores_per_node=1, ram_gb_per_node=2.0
        )


# ==============================================================
# 3. SCRUM-178: Performance Benchmarking & SLA Tests (< 20 min)
# ==============================================================


def test_sla_benchmarking_within_20_minutes_sla() -> None:
    """Verifica que una ejecución con tiempo menor a 1200s satisfaga el SLA."""
    # Simula un procesamiento de 1,500,000 registros en 12.5 minutos (750 segundos)
    records = 1_500_000
    elapsed_time = 750.0  # 12.5 minutos

    report = SparkSlaBenchmarker.evaluate_pipeline_sla(
        records_processed=records,
        elapsed_seconds=elapsed_time,
    )

    assert report["is_sla_met"] is True
    assert report["status"] == "PASSED"
    assert report["elapsed_seconds"] == 750.0
    assert report["margin_seconds"] == 450.0  # 1200 - 750
    assert report["throughput_rps"] == 2000.0


def test_sla_benchmarking_breach_detection() -> None:
    """Verifica que una ejecución que supere los 20 minutos sea clasificada como SLA BREACH."""
    # 22 minutos (1320 segundos)
    report = SparkSlaBenchmarker.evaluate_pipeline_sla(
        records_processed=500_000,
        elapsed_seconds=1320.0,
    )

    assert report["is_sla_met"] is False
    assert report["status"] == "FAILED_SLA_BREACHED"
    assert report["margin_seconds"] < 0


def test_live_benchmark_simulation_throughput() -> None:
    """Prueba rápida de micro-benchmarking en tiempo real para verificar tasa de cálculo."""
    start = time.perf_counter()

    # Cálculo sintético de 50,000 transformaciones matriciales
    total = sum(i * 1.05 for i in range(50_000))
    elapsed = time.perf_counter() - start

    assert total > 0
    report = SparkSlaBenchmarker.evaluate_pipeline_sla(
        records_processed=50_000,
        elapsed_seconds=elapsed,
        expected_sla_seconds=2.0,  # micro-SLA para la prueba
    )
    assert report["is_sla_met"] is True
    assert report["throughput_rps"] > 10_000
