"""AgroSense Café — PySpark Job Tuning, Resource Allocation and Optimization Engine.

Implements requirements for:
- SCRUM-176: Execution plan tuning, broadcast join optimizer, partition strategy.
- SCRUM-177: EMR / Spark Cluster memory and core resource allocation calculator.
- SCRUM-178: SLA validation and execution plan metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClusterWorkloadProfile(str, Enum):
    """Perfiles de carga para clústeres Spark en AgroSense."""

    LIGHT = "LIGHT"  # Ingestas satelitales / tareas ad-hoc (< 100K registros)
    STANDARD = "STANDARD"  # Ventanas móviles diarias / normalización NPK (100K - 5M registros)
    HEAVY = "HEAVY"  # Reprocesamiento histórico / Backfill masivo multi-anual (> 5M registros)


@dataclass(frozen=True)
class SparkClusterResourceConfig:
    """Configuración de memoria y núcleos para Spark en AWS EMR Serverless / EC2."""

    executor_instances: int
    executor_cores: int
    executor_memory_mb: int
    executor_memory_overhead_mb: int
    driver_cores: int
    driver_memory_mb: int
    default_parallelism: int
    sql_shuffle_partitions: int
    extra_spark_conf: dict[str, str] = field(default_factory=dict)

    @property
    def total_executor_memory_mb(self) -> int:
        return self.executor_memory_mb + self.executor_memory_overhead_mb

    def to_spark_conf_dict(self) -> dict[str, str]:
        """Genera el diccionario de parámetros estándar para spark-submit o SparkSession."""
        base_conf = {
            "spark.executor.instances": str(self.executor_instances),
            "spark.executor.cores": str(self.executor_cores),
            "spark.executor.memory": f"{self.executor_memory_mb}m",
            "spark.executor.memoryOverhead": f"{self.executor_memory_overhead_mb}m",
            "spark.driver.cores": str(self.driver_cores),
            "spark.driver.memory": f"{self.driver_memory_mb}m",
            "spark.default.parallelism": str(self.default_parallelism),
            "spark.sql.shuffle.partitions": str(self.sql_shuffle_partitions),
            # Optimizaciones de SCRUM-176 (Adaptive Query Execution y Broadcast)
            "spark.sql.adaptive.enabled": "true",
            "spark.sql.adaptive.coalescePartitions.enabled": "true",
            "spark.sql.adaptive.skewJoin.enabled": "true",
            "spark.sql.autoBroadcastJoinThreshold": "10485760",  # 10 MB threshold
            "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
            "spark.kryoserializer.buffer.max": "512m",
        }
        base_conf.update(self.extra_spark_conf)
        return base_conf


class SparkResourceAllocationOptimizer:
    """Calculadora de asignación óptima de recursos para clústeres Spark / EMR (SCRUM-177).

    Aplica las mejores prácticas de la industria:
    - 1 Core reservado por nodo para el sistema operativo y daemons de Hadoop/YARN.
    - 4 a 5 Cores por executor para evitar el cuello de botella de I/O en HDFS/S3.
    - 7-10% de memoria asignada a `executor.memoryOverhead` (mínimo 384 MB).
    - 1 Executor reservado para el Application Master (YARN / EMR).
    """

    @classmethod
    def calculate_emr_cluster_specs(
        cls,
        node_count: int,
        cores_per_node: int,
        ram_gb_per_node: float,
        overhead_fraction: float = 0.10,
    ) -> SparkClusterResourceConfig:
        if node_count < 1 or cores_per_node < 2 or ram_gb_per_node < 4:
            raise ValueError("Configuración de hardware mínima inválida para clúster Spark.")

        # 1. Cores disponibles por nodo (dejando 1 core para el SO / daemons)
        usable_cores_per_node = max(cores_per_node - 1, 1)

        # Usar 4 ó 5 cores por executor (regla dorada de Spark para throughput en S3)
        cores_per_executor = 4 if usable_cores_per_node % 4 == 0 or usable_cores_per_node >= 8 else min(usable_cores_per_node, 5)

        executors_per_node = usable_cores_per_node // cores_per_executor
        if executors_per_node == 0:
            executors_per_node = 1
            cores_per_executor = usable_cores_per_node

        # 2. Total executors en el clúster menos 1 para el ApplicationMaster
        total_executors = max((executors_per_node * node_count) - 1, 1)

        # 3. Memoria disponible por nodo en MB (dejando 1 GB para el SO)
        usable_ram_mb = int((ram_gb_per_node - 1.0) * 1024)
        total_mem_per_executor = usable_ram_mb // executors_per_node

        # 4. Dividir entre Heap y Overhead
        overhead_mb = max(int(total_mem_per_executor * overhead_fraction), 384)
        executor_heap_mb = total_mem_per_executor - overhead_mb

        # 5. Paralelismo por defecto (2 a 3 tareas por core de executor)
        total_cores = total_executors * cores_per_executor
        default_parallelism = total_cores * 2
        shuffle_partitions = total_cores * 3

        return SparkClusterResourceConfig(
            executor_instances=total_executors,
            executor_cores=cores_per_executor,
            executor_memory_mb=executor_heap_mb,
            executor_memory_overhead_mb=overhead_mb,
            driver_cores=cores_per_executor,
            driver_memory_mb=executor_heap_mb,
            default_parallelism=default_parallelism,
            sql_shuffle_partitions=shuffle_partitions,
        )

    @classmethod
    def get_preset_for_workload(
        cls, profile: ClusterWorkloadProfile
    ) -> SparkClusterResourceConfig:
        """Devuelve configuraciones estándar pre-optimizadas según el volumen de datos."""
        if profile == ClusterWorkloadProfile.LIGHT:
            # 2 nodos m5.xlarge (4 vCPU, 16 GB RAM)
            return cls.calculate_emr_cluster_specs(
                node_count=2, cores_per_node=4, ram_gb_per_node=16.0
            )
        elif profile == ClusterWorkloadProfile.STANDARD:
            # 4 nodos m5.2xlarge (8 vCPU, 32 GB RAM)
            return cls.calculate_emr_cluster_specs(
                node_count=4, cores_per_node=8, ram_gb_per_node=32.0
            )
        else:  # HEAVY
            # 8 nodos r5.2xlarge (8 vCPU, 64 GB RAM)
            return cls.calculate_emr_cluster_specs(
                node_count=8, cores_per_node=8, ram_gb_per_node=64.0
            )


class SparkExecutionPlanOptimizer:
    """Optimizador de planes de ejecución y estrategias de partición (SCRUM-176)."""

    BROADCAST_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB

    @classmethod
    def should_broadcast(cls, estimated_dataset_size_bytes: int) -> bool:
        """Determina si un dataset es apto para Broadcast Hash Join (evitando shuffle)."""
        return 0 < estimated_dataset_size_bytes <= cls.BROADCAST_THRESHOLD_BYTES

    @classmethod
    def calculate_optimal_partitions(
        cls,
        total_data_size_bytes: int,
        target_partition_size_mb: int = 128,
        min_partitions: int = 8,
    ) -> int:
        """Calcula el número óptimo de particiones para balancear carga y evitar skew.

        Regla estándar de Spark: Particiones en memoria de 128 MB a 200 MB por bloque.
        """
        target_bytes = target_partition_size_mb * 1024 * 1024
        computed = math.ceil(total_data_size_bytes / target_bytes)
        return max(computed, min_partitions)

    @classmethod
    def partition_strategy_for_agronomic_lake(
        cls,
        layer: str,
    ) -> list[str]:
        """Define la clave de particionamiento óptima según la capa del Medallion Lake."""
        layer_upper = layer.upper()
        if layer_upper in ("SILVER", "INTERMEDIATE"):
            # Particionado por fecha de observación para lectura secuencial diaria
            return ["observation_date"]
        elif layer_upper in ("GOLD", "REFINED"):
            # Particionado por año, mes y finca para optimización de consultas ad-hoc Athena
            return ["year", "month", "farm_id"]
        return ["date"]


class SparkSlaBenchmarker:
    """Verificador y validador de SLA para trabajos de procesamiento distribuido (SCRUM-178)."""

    MAX_SLA_MINUTES = 20.0
    MAX_SLA_SECONDS = 20.0 * 60.0  # 1200s

    @classmethod
    def evaluate_pipeline_sla(
        cls,
        records_processed: int,
        elapsed_seconds: float,
        expected_sla_seconds: float = MAX_SLA_SECONDS,
    ) -> dict[str, Any]:
        """Evalúa si la ejecución cumplió estrictamente el SLA de procesamiento (< 20 min)."""
        throughput_records_per_sec = (
            records_processed / elapsed_seconds if elapsed_seconds > 0 else 0.0
        )
        is_sla_met = elapsed_seconds <= expected_sla_seconds

        return {
            "is_sla_met": is_sla_met,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "max_allowed_sla_seconds": expected_sla_seconds,
            "margin_seconds": round(expected_sla_seconds - elapsed_seconds, 2),
            "records_processed": records_processed,
            "throughput_rps": round(throughput_records_per_sec, 2),
            "status": "PASSED" if is_sla_met else "FAILED_SLA_BREACHED",
        }
