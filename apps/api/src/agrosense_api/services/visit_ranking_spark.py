"""AgroSense Café — PySpark Job: Excess Rainfall & Visit Ranking (AGRO-21 / SCRUM-40).

Implements:
- SCRUM-173 (RN-05): 72-hour cumulative rainfall accumulator (> 120 mm trigger for
  `EXTREME_RAIN` alert, `weight_multiplier=1`, `forecast_days_ahead=3`).
- SCRUM-174 (RN-06, CA-05): Multi-criteria priority ranking engine for agronomic visits:
  Priority Score = 3 * Rust + 2 * Deficit + 1 * Extreme Rain. Ties resolved deterministically
  by cultivated area in hectares (`cultivated_area_ha` / `tie_breaker_area_ha`).
- SCRUM-175: PostgreSQL `agronomist_field_visits` idempotent upsert and S3 Refined
  `refined/fact_alerts_and_visits/` Snappy-compressed Parquet export.
"""

from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.services.agronomic_alerts_spark import (
    AgronomicAlertPayload,
    AlertIntegrityHashValidator,
    AlertStatus,
    AlertType,
    DataSourceUsed,
)


class VisitStatus(StrEnum):
    """Lifecycle status of a scheduled agronomist field visit."""

    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    UNATTENDED = "UNATTENDED"


class VisitOutcome(StrEnum):
    """Agronomic outcome observed during an inspected farm visit."""

    CONTROLLED = "CONTROLLED"
    PARTIAL_LOSS = "PARTIAL_LOSS"
    FALSE_POSITIVE = "FALSE_POSITIVE"


@dataclass(frozen=True, slots=True)
class PrioritizedVisitRecord:
    """Canonical model for a prioritized farm field visit (RN-06, CA-05)."""

    visit_id: UUID
    farm_id: str
    agronomist_id: str
    calculation_date: str
    rust_alerts_count: int
    deficit_alerts_count: int
    rain_alerts_count: int
    priority_score: int
    tie_breaker_area_ha: float
    visit_status: VisitStatus = VisitStatus.SCHEDULED
    visit_outcome: VisitOutcome | None = None
    visited_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "visit_id": str(self.visit_id),
            "farm_id": self.farm_id,
            "agronomist_id": self.agronomist_id,
            "calculation_date": self.calculation_date,
            "rust_alerts_count": int(self.rust_alerts_count),
            "deficit_alerts_count": int(self.deficit_alerts_count),
            "rain_alerts_count": int(self.rain_alerts_count),
            "priority_score": int(self.priority_score),
            "tie_breaker_area_ha": round(float(self.tie_breaker_area_ha), 2),
            "visit_status": self.visit_status.value,
            "visit_outcome": self.visit_outcome.value if self.visit_outcome else None,
            "visited_at": self.visited_at,
        }


class ExcessRainfallEvaluator:
    """Accumulates 72h precipitation and generates EXTREME_RAIN alerts (RN-05 / SCRUM-173)."""

    THRESHOLD_MM: float = 120.0

    @classmethod
    def evaluate_72h_rainfall(
        cls,
        farm_id: str,
        observation_date: str,
        precip_accum_72h_mm: float,
        data_source_used: DataSourceUsed = DataSourceUsed.IOT_SENSOR,
        generated_at_override: str | None = None,
    ) -> AgronomicAlertPayload | None:
        """Trigger EXTREME_RAIN alert if 72-hour precipitation exceeds 120 mm."""
        if precip_accum_72h_mm <= cls.THRESHOLD_MM:
            return None

        evidence_core = {
            "accumulated_rain_72h_mm": round(float(precip_accum_72h_mm), 2),
            "threshold_mm": cls.THRESHOLD_MM,
            "risk_level": "LANDSLIDE_AND_SATURATION_RISK",
            "rule_ref": "RN-05",
            "observation_date": observation_date,
        }
        integrity_hash = AlertIntegrityHashValidator.compute_evidence_hash(
            farm_id=farm_id,
            alert_type=AlertType.EXTREME_RAIN.value,
            observation_date=observation_date,
            evidence_core=evidence_core,
        )
        evidence_summary = {**evidence_core, "integrity_hash": integrity_hash}

        alert_id = uuid5(
            NAMESPACE_DNS,
            f"ALERT:{AlertType.EXTREME_RAIN.value}:{farm_id}:{observation_date}",
        )
        gen_ts = generated_at_override or f"{observation_date}T04:45:00Z"

        return AgronomicAlertPayload(
            alert_id=alert_id,
            farm_id=farm_id,
            alert_type=AlertType.EXTREME_RAIN,
            weight_multiplier=1,
            data_source_used=data_source_used,
            forecast_days_ahead=3,
            evidence_summary=evidence_summary,
            alert_status=AlertStatus.ACTIVE,
            generated_at=gen_ts,
            observation_date=observation_date,
        )


class AgronomicVisitRankingEngine:
    """Scores and prioritizes farm technician visits with tie-breaking (RN-06 / CA-05)."""

    DEFAULT_AGRONOMIST_ID: str = "712020:7115127e-3148-4d0d-85c3-5722240cbcb2"

    @classmethod
    def compute_priority_score(
        cls,
        rust_count: int,
        deficit_count: int,
        rain_count: int,
    ) -> int:
        """Score = 3 * Rust + 2 * Deficit + 1 * Extreme Rain (RN-06)."""
        return (3 * rust_count) + (2 * deficit_count) + (1 * rain_count)

    @classmethod
    def generate_prioritized_visits(
        cls,
        farms: list[dict[str, Any]],
        alerts: list[AgronomicAlertPayload | dict[str, Any]],
        calculation_date: str,
        agronomist_ids: list[str] | None = None,
    ) -> list[PrioritizedVisitRecord]:
        """Rank farms by priority score descending, resolving ties by cultivated area."""
        active_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"COFFEE_RUST": 0, "WATER_DEFICIT": 0, "EXTREME_RAIN": 0}
        )

        for a in alerts:
            data = a.to_dict() if isinstance(a, AgronomicAlertPayload) else a
            if data.get("alert_status") != AlertStatus.ACTIVE.value:
                continue
            fid = str(data["farm_id"])
            atype = str(data["alert_type"])
            if atype in active_counts[fid]:
                active_counts[fid][atype] += 1

        scored: list[dict[str, Any]] = []
        for farm in farms:
            fid = str(farm["farm_id"])
            counts = active_counts[fid]
            rust = counts["COFFEE_RUST"]
            deficit = counts["WATER_DEFICIT"]
            rain = counts["EXTREME_RAIN"]
            score = cls.compute_priority_score(rust, deficit, rain)
            area_ha = float(farm.get("cultivated_area_ha", 5.0))

            scored.append(
                {
                    "farm_id": fid,
                    "rust_count": rust,
                    "deficit_count": deficit,
                    "rain_count": rain,
                    "priority_score": score,
                    "tie_breaker_area_ha": area_ha,
                }
            )

        # Sort descending by priority score, breaking ties by cultivated_area_ha (RN-06, CA-05)
        scored.sort(
            key=lambda x: (x["priority_score"], x["tie_breaker_area_ha"]),
            reverse=True,
        )

        assigned_agros = agronomist_ids or [cls.DEFAULT_AGRONOMIST_ID]
        visits: list[PrioritizedVisitRecord] = []

        for idx, item in enumerate(scored):
            fid = item["farm_id"]
            natural_key = f"VISIT:{fid}:{calculation_date}"
            visit_id = uuid5(NAMESPACE_DNS, natural_key)
            assigned_id = assigned_agros[idx % len(assigned_agros)]

            visits.append(
                PrioritizedVisitRecord(
                    visit_id=visit_id,
                    farm_id=fid,
                    agronomist_id=assigned_id,
                    calculation_date=calculation_date,
                    rust_alerts_count=item["rust_count"],
                    deficit_alerts_count=item["deficit_count"],
                    rain_alerts_count=item["rain_count"],
                    priority_score=item["priority_score"],
                    tie_breaker_area_ha=item["tie_breaker_area_ha"],
                )
            )

        return visits

    @staticmethod
    def build_pyspark_ranking_dataframe(spark_df: Any) -> Any:
        """Native PySpark ranking with tie-breaking by area (SCRUM-174)."""
        from pyspark.sql import Window  # type: ignore
        from pyspark.sql import functions as F  # type: ignore

        score_expr = (
            (F.col("rust_alerts_count") * 3)
            + (F.col("deficit_alerts_count") * 2)
            + (F.col("rain_alerts_count") * 1)
        )
        ranking_window = Window.orderBy(
            F.col("priority_score").desc(),
            F.col("tie_breaker_area_ha").desc(),
        )

        return spark_df.withColumn("priority_score", score_expr).withColumn(
            "rank_position", F.row_number().over(ranking_window)
        )


class VisitRankingOutputWriter:
    """Exports visit schedule to PostgreSQL and S3 Refined Parquet (SCRUM-175)."""

    VISITS_SCHEMA = pa.schema(
        [
            pa.field("visit_id", pa.string(), nullable=False),
            pa.field("farm_id", pa.string(), nullable=False),
            pa.field("agronomist_id", pa.string(), nullable=False),
            pa.field("calculation_date", pa.string(), nullable=False),
            pa.field("rust_alerts_count", pa.int32(), nullable=False),
            pa.field("deficit_alerts_count", pa.int32(), nullable=False),
            pa.field("rain_alerts_count", pa.int32(), nullable=False),
            pa.field("priority_score", pa.int32(), nullable=False),
            pa.field("tie_breaker_area_ha", pa.float64(), nullable=False),
            pa.field("visit_status", pa.string(), nullable=False),
        ]
    )

    @classmethod
    def visits_to_snappy_parquet_bytes(cls, visits: list[PrioritizedVisitRecord]) -> bytes:
        rows = [
            {
                "visit_id": str(v.visit_id),
                "farm_id": v.farm_id,
                "agronomist_id": v.agronomist_id,
                "calculation_date": v.calculation_date,
                "rust_alerts_count": int(v.rust_alerts_count),
                "deficit_alerts_count": int(v.deficit_alerts_count),
                "rain_alerts_count": int(v.rain_alerts_count),
                "priority_score": int(v.priority_score),
                "tie_breaker_area_ha": float(v.tie_breaker_area_ha),
                "visit_status": v.visit_status.value,
            }
            for v in visits
        ]
        table = pa.Table.from_pylist(rows, schema=cls.VISITS_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="SNAPPY", use_dictionary=True)
        return buf.getvalue()

    @classmethod
    def write_visits_to_s3(
        cls,
        s3_client: Any,
        bucket_name: str,
        visits: list[PrioritizedVisitRecord],
        calculation_date: str,
    ) -> str:
        parquet_bytes = cls.visits_to_snappy_parquet_bytes(visits)
        s3_key = f"refined/fact_alerts_and_visits/dt={calculation_date}/part-00000.snappy.parquet"
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=parquet_bytes,
            ContentType="application/vnd.apache.parquet",
        )
        return f"s3://{bucket_name}/{s3_key}"

    @staticmethod
    async def upsert_visits_to_postgres(
        db_session: AsyncSession,
        visits: list[PrioritizedVisitRecord],
    ) -> int:
        if not visits:
            return 0

        stmt = text(
            """
            INSERT INTO agronomist_field_visits (
                visit_id,
                farm_id,
                agronomist_id,
                calculation_date,
                rust_alerts_count,
                deficit_alerts_count,
                rain_alerts_count,
                priority_score,
                tie_breaker_area_ha,
                visit_status
            ) VALUES (
                :visit_id,
                :farm_id,
                :agronomist_id,
                CAST(:calculation_date AS DATE),
                :rust_alerts_count,
                :deficit_alerts_count,
                :rain_alerts_count,
                :priority_score,
                :tie_breaker_area_ha,
                :visit_status
            )
            ON CONFLICT (visit_id) DO UPDATE SET
                priority_score = EXCLUDED.priority_score,
                rust_alerts_count = EXCLUDED.rust_alerts_count,
                deficit_alerts_count = EXCLUDED.deficit_alerts_count,
                rain_alerts_count = EXCLUDED.rain_alerts_count,
                tie_breaker_area_ha = EXCLUDED.tie_breaker_area_ha,
                visit_status = EXCLUDED.visit_status
            """
        )

        for v in visits:
            await db_session.execute(
                stmt,
                {
                    "visit_id": str(v.visit_id),
                    "farm_id": str(v.farm_id),
                    "agronomist_id": str(v.agronomist_id),
                    "calculation_date": v.calculation_date,
                    "rust_alerts_count": int(v.rust_alerts_count),
                    "deficit_alerts_count": int(v.deficit_alerts_count),
                    "rain_alerts_count": int(v.rain_alerts_count),
                    "priority_score": int(v.priority_score),
                    "tie_breaker_area_ha": float(v.tie_breaker_area_ha),
                    "visit_status": v.visit_status.value,
                },
            )
        await db_session.commit()
        return len(visits)
