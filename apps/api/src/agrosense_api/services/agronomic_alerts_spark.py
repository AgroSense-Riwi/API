"""AgroSense Café — PySpark Rolling Window: Coffee Rust & Water Deficit Alerts (AGRO-20).

Implements:
- SCRUM-165 (RN-03, CA-03): 4-day rolling window aggregator evaluating relative humidity > 85%
  for >= 6 hours/day and daily mean temperature between 21°C and 25°C to trigger early
  `COFFEE_RUST` alerts (`weight_multiplier=3`, `forecast_days_ahead=5`), with automatic fallback
  to NASA POWER / Open-Meteo satellite data when IoT sensors are stuck or missing (`RN-02`/`RN-03`).
- RN-04 & CA-04: 5-day rolling window aggregator evaluating mean soil moisture < 25% combined
  with Open-Meteo 5-day ahead cumulative precipitation forecast (< 5 mm triggers `ACTIVE`
  `WATER_DEFICIT` with `weight_multiplier=2`; >= 5 mm suppresses/cancels the alert as `EXPIRED`
  with `action=SUPPRESSED_DUE_TO_RAIN_FORECAST`).
- SCRUM-169 (RNF-02): Alert event payload formatter, Snappy-compressed Apache Parquet writer for
  `trusted/agroclimatic_daily_features/`, AWS Glue partition registrar, and idempotent PostgreSQL
  `agronomic_alerts` upsert writer.
- SCRUM-164: Deterministic SHA-256 evidence payload integrity hash builder and verifier.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.services.s3_medallion_parquet import GlueDataCatalogManager


class AlertType(StrEnum):
    """Canonical agronomic alert identifiers (RN-03, RN-04, RN-05)."""

    COFFEE_RUST = "COFFEE_RUST"
    WATER_DEFICIT = "WATER_DEFICIT"
    EXTREME_RAIN = "EXTREME_RAIN"


class AlertStatus(StrEnum):
    """Lifecycle states for agronomic alerts in PostgreSQL (`agronomic_alerts`)."""

    ACTIVE = "ACTIVE"
    ATTENDED = "ATTENDED"
    EXPIRED = "EXPIRED"


class DataSourceUsed(StrEnum):
    """Primary telemetry source used to evaluate an agronomic rule (RN-03)."""

    IOT_SENSOR = "IOT_SENSOR"
    SATELLITE_FALLBACK = "SATELLITE_FALLBACK"


@dataclass(frozen=True, slots=True)
class RollingWindowThresholds:
    """Configurable agronomic rule thresholds for RN-03, RN-04, and CA-04."""

    rust_window_days: int = 4
    rust_rh_threshold_pct: float = 85.0
    rust_min_hours_per_day: int = 6
    rust_temp_min_c: float = 21.0
    rust_temp_max_c: float = 25.0
    rust_weight_multiplier: int = 3

    deficit_window_days: int = 5
    deficit_soil_moisture_max_pct: float = 25.0
    deficit_forecast_rain_max_mm: float = 5.0
    deficit_weight_multiplier: int = 2

    forecast_days_ahead: int = 5


@dataclass(frozen=True, slots=True)
class AgroclimaticDailyFeature:
    """Silver-layer daily feature row per farm (`trusted/agroclimatic_daily_features/`)."""

    farm_id: str
    observation_date: str
    high_humidity_hours_gt85: int
    daily_mean_temp_c: float
    soil_moisture_mean_pct: float
    precip_accum_72h_mm: float
    forecast_rain_next_5d_mm: float
    consecutive_rust_days: int
    consecutive_dry_soil_days: int
    primary_source_used: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "farm_id": self.farm_id,
            "observation_date": self.observation_date,
            "high_humidity_hours_gt85": int(self.high_humidity_hours_gt85),
            "daily_mean_temp_c": round(float(self.daily_mean_temp_c), 2),
            "soil_moisture_mean_pct": round(float(self.soil_moisture_mean_pct), 2),
            "precip_accum_72h_mm": round(float(self.precip_accum_72h_mm), 2),
            "forecast_rain_next_5d_mm": round(float(self.forecast_rain_next_5d_mm), 2),
            "consecutive_rust_days": int(self.consecutive_rust_days),
            "consecutive_dry_soil_days": int(self.consecutive_dry_soil_days),
            "primary_source_used": self.primary_source_used,
        }


@dataclass(frozen=True, slots=True)
class AgronomicAlertPayload:
    """Formatted agronomic alert event ready for PostgreSQL and S3 persistence (SCRUM-169)."""

    alert_id: UUID
    farm_id: str
    alert_type: AlertType
    weight_multiplier: int
    data_source_used: DataSourceUsed
    forecast_days_ahead: int
    evidence_summary: dict[str, Any]
    alert_status: AlertStatus
    generated_at: str
    observation_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": str(self.alert_id),
            "farm_id": self.farm_id,
            "alert_type": self.alert_type.value,
            "weight_multiplier": self.weight_multiplier,
            "data_source_used": self.data_source_used.value,
            "forecast_days_ahead": self.forecast_days_ahead,
            "evidence_summary": dict(self.evidence_summary),
            "alert_status": self.alert_status.value,
            "generated_at": self.generated_at,
            "observation_date": self.observation_date,
        }


class AlertIntegrityHashValidator:
    """Deterministic SHA-256 hash builder and verifier for alert evidence payloads (SCRUM-164)."""

    @staticmethod
    def compute_evidence_hash(
        farm_id: str,
        alert_type: str,
        observation_date: str,
        evidence_core: dict[str, Any],
    ) -> str:
        """Compute canonical SHA-256 hex digest over alert identity and core metrics."""
        filtered_core = {k: v for k, v in sorted(evidence_core.items()) if k != "integrity_hash"}
        canonical_payload = json.dumps(
            {
                "farm_id": str(farm_id),
                "alert_type": str(alert_type),
                "observation_date": str(observation_date),
                "evidence": filtered_core,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    @classmethod
    def verify_alert_payload(cls, alert: dict[str, Any] | AgronomicAlertPayload) -> bool:
        """Verify that an alert payload's embedded `integrity_hash` matches its metrics."""
        data = alert.to_dict() if isinstance(alert, AgronomicAlertPayload) else dict(alert)
        evidence = data.get("evidence_summary") or {}
        expected_hash = evidence.get("integrity_hash")
        if not expected_hash or not isinstance(expected_hash, str):
            return False

        obs_date = str(
            data.get("observation_date")
            or evidence.get("observation_date")
            or str(data.get("generated_at", ""))[:10]
        )
        recalculated = cls.compute_evidence_hash(
            farm_id=str(data["farm_id"]),
            alert_type=str(data["alert_type"]),
            observation_date=obs_date,
            evidence_core=evidence,
        )
        return recalculated == expected_hash


class SatelliteFallbackNormalizer:
    """Merges Silver IoT telemetry with NASA POWER and Open-Meteo Bronze payloads (RN-03)."""

    @staticmethod
    def extract_nasa_power_daily_index(
        nasa_payloads: list[dict[str, Any]] | dict[str, Any] | None,
    ) -> dict[tuple[str, str], dict[str, float]]:
        """Index NASA POWER daily metrics by `(farm_id, YYYY-MM-DD)`."""
        index: dict[tuple[str, str], dict[str, float]] = {}
        if not nasa_payloads:
            return index

        envelopes = [nasa_payloads] if isinstance(nasa_payloads, dict) else list(nasa_payloads)
        for env in envelopes:
            exec_date = str(env.get("execution_date", ""))
            for rec in env.get("records", []):
                if rec.get("status") not in (None, "SUCCESS"):
                    continue
                farm_id = str(rec.get("farm_id") or rec.get("farm_code") or "")
                if not farm_id:
                    continue
                params = rec.get("data", {}).get("properties", {}).get("parameter", {})
                t2m_map = params.get("T2M_MEAN") or params.get("T2M") or {}
                rh2m_map = params.get("RH2M") or {}
                prec_map = params.get("PRECTOTCORR") or {}

                date_keys = set(t2m_map.keys()) | set(rh2m_map.keys()) | set(prec_map.keys())
                if not date_keys and exec_date:
                    date_keys = {exec_date.replace("-", "")}

                for raw_dt in date_keys:
                    clean = str(raw_dt).replace("-", "")
                    iso_dt = (
                        f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}" if len(clean) == 8 else exec_date
                    )
                    if not iso_dt:
                        continue
                    index[(farm_id, iso_dt)] = {
                        "temp_c": float(t2m_map.get(raw_dt, t2m_map.get(clean, 22.5))),
                        "rh_pct": float(rh2m_map.get(raw_dt, rh2m_map.get(clean, 75.0))),
                        "precip_mm": float(prec_map.get(raw_dt, prec_map.get(clean, 0.0))),
                    }
        return index

    @staticmethod
    def extract_open_meteo_index(
        open_meteo_payloads: list[dict[str, Any]] | dict[str, Any] | None,
        rh_threshold_pct: float = 85.0,
    ) -> dict[tuple[str, str], dict[str, float]]:
        """Index Open-Meteo 5-day rain forecast and hourly RH > 85% counts by `(farm_id, date)`."""
        index: dict[tuple[str, str], dict[str, float]] = {}
        if not open_meteo_payloads:
            return index

        envelopes = (
            [open_meteo_payloads]
            if isinstance(open_meteo_payloads, dict)
            else list(open_meteo_payloads)
        )
        for env in envelopes:
            exec_date = str(env.get("execution_date", ""))
            for rec in env.get("records", []):
                if rec.get("status") not in (None, "SUCCESS"):
                    continue
                farm_id = str(rec.get("farm_id") or rec.get("farm_code") or "")
                if not farm_id:
                    continue

                data = rec.get("data", {})
                daily = data.get("daily", {})
                daily_times: list[str] = list(daily.get("time") or [])
                precip_sums: list[float] = [
                    float(x) for x in (daily.get("precipitation_sum") or [])
                ]
                temp_means: list[float] = [
                    float(x) for x in (daily.get("temperature_2m_mean") or [])
                ]

                forecast_5d_mm = round(sum(precip_sums[:5]), 2) if precip_sums else 0.0

                hourly = data.get("hourly", {})
                hourly_times: list[str] = list(hourly.get("time") or [])
                hourly_rh: list[float] = [
                    float(x) for x in (hourly.get("relative_humidity_2m") or [])
                ]
                rh_hours_by_day: dict[str, int] = defaultdict(int)
                for ts_str, rh_val in zip(hourly_times, hourly_rh, strict=False):
                    day_key = str(ts_str)[:10]
                    if rh_val > rh_threshold_pct:
                        rh_hours_by_day[day_key] += 1

                target_dates = set(daily_times) | set(rh_hours_by_day.keys())
                if exec_date:
                    target_dates.add(exec_date)

                for idx, dt_str in enumerate(sorted(target_dates)):
                    day_temp = (
                        temp_means[idx]
                        if idx < len(temp_means)
                        else (temp_means[0] if temp_means else 22.5)
                    )
                    day_precip = precip_sums[idx] if idx < len(precip_sums) else 0.0
                    index[(farm_id, dt_str)] = {
                        "forecast_rain_next_5d_mm": forecast_5d_mm,
                        "high_humidity_hours_gt85": float(rh_hours_by_day.get(dt_str, 0)),
                        "temp_c": float(day_temp),
                        "precip_mm": float(day_precip),
                    }
        return index

    @classmethod
    def build_daily_farm_inputs(
        cls,
        iot_readings: list[dict[str, Any]],
        nasa_payloads: list[dict[str, Any]] | dict[str, Any] | None = None,
        open_meteo_payloads: list[dict[str, Any]] | dict[str, Any] | None = None,
        rh_threshold_pct: float = 85.0,
    ) -> list[dict[str, Any]]:
        """Aggregate IoT readings per `(farm_id, date)` with satellite fallback (`RN-03`)."""
        nasa_idx = cls.extract_nasa_power_daily_index(nasa_payloads)
        meteo_idx = cls.extract_open_meteo_index(open_meteo_payloads, rh_threshold_pct)

        # Handle pre-aggregated records
        if iot_readings and (
            "daily_mean_temp_c" in iot_readings[0]
            or "high_humidity_hours_gt85" in iot_readings[0]
            or "soil_moisture_mean_pct" in iot_readings[0]
        ):
            enriched: list[dict[str, Any]] = []
            for row in iot_readings:
                farm_id = str(row["farm_id"])
                obs_date = str(row.get("observation_date") or row.get("partition_date"))[:10]
                is_stuck = bool(row.get("is_sensor_stuck_24h", False))
                is_valid = bool(row.get("is_valid_for_alerts", not is_stuck))

                sat_nasa = nasa_idx.get((farm_id, obs_date), {})
                sat_meteo = meteo_idx.get((farm_id, obs_date), {})

                use_fallback = (
                    not is_valid
                    or row.get("primary_source_used") == DataSourceUsed.SATELLITE_FALLBACK.value
                    or (row.get("daily_mean_temp_c") is None and bool(sat_nasa or sat_meteo))
                )

                if use_fallback:
                    temp_c = float(
                        sat_nasa.get(
                            "temp_c",
                            sat_meteo.get("temp_c", row.get("daily_mean_temp_c") or 23.0),
                        )
                    )
                    if (
                        "high_humidity_hours_gt85" in sat_meteo
                        and sat_meteo["high_humidity_hours_gt85"] > 0
                    ):
                        rh_hours = int(sat_meteo["high_humidity_hours_gt85"])
                    elif sat_nasa.get("rh_pct", 0.0) > rh_threshold_pct:
                        rh_hours = 8
                    else:
                        rh_hours = int(row.get("high_humidity_hours_gt85") or 0)
                    source_used = DataSourceUsed.SATELLITE_FALLBACK.value
                else:
                    temp_c = float(row.get("daily_mean_temp_c", 23.0))
                    rh_hours = int(row.get("high_humidity_hours_gt85", 0))
                    source_used = str(
                        row.get("primary_source_used") or DataSourceUsed.IOT_SENSOR.value
                    )

                soil_moisture = float(row.get("soil_moisture_mean_pct", 35.0))
                precip_mm = float(
                    row.get(
                        "daily_precip_mm",
                        row.get("precip_accum_72h_mm", sat_nasa.get("precip_mm", 0.0)),
                    )
                )
                forecast_5d = float(
                    row.get(
                        "forecast_rain_next_5d_mm",
                        sat_meteo.get("forecast_rain_next_5d_mm", 0.0),
                    )
                )

                enriched.append(
                    {
                        "farm_id": farm_id,
                        "observation_date": obs_date,
                        "high_humidity_hours_gt85": rh_hours,
                        "daily_mean_temp_c": temp_c,
                        "soil_moisture_mean_pct": soil_moisture,
                        "daily_precip_mm": precip_mm,
                        "forecast_rain_next_5d_mm": forecast_5d,
                        "primary_source_used": source_used,
                    }
                )
            return enriched

        # Otherwise aggregate from raw / silver IoT readings
        grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
            lambda: {
                "temp": [],
                "rh_hours": [],
                "rh_vals": [],
                "soil": [],
                "precip": [],
            }
        )
        all_keys: set[tuple[str, str]] = set(nasa_idx.keys()) | set(meteo_idx.keys())

        for r in iot_readings:
            farm_id = str(r.get("farm_id", ""))
            if not farm_id:
                continue
            raw_dt = (
                r.get("partition_date")
                or r.get("observation_date")
                or r.get("corrected_timestamp")
                or r.get("device_timestamp_raw")
                or ""
            )
            obs_date = str(raw_dt)[:10]
            if not obs_date:
                continue
            all_keys.add((farm_id, obs_date))

            is_stuck = bool(r.get("is_sensor_stuck_24h", False))
            is_valid = bool(r.get("is_valid_for_alerts", not is_stuck))
            if not is_valid or is_stuck:
                continue

            stype = str(r.get("sensor_type", "")).upper()
            val = float(
                r.get("standardized_value", r.get("raw_value", r.get("reading_value", 0.0)))
            )
            bucket = grouped[(farm_id, obs_date)]

            if "TEMP" in stype:
                bucket["temp"].append(val)
            elif "HUMIDITY" in stype or stype in {"RH", "RELATIVE_HUMIDITY"}:
                bucket["rh_vals"].append(val)
                if val > rh_threshold_pct:
                    bucket["rh_hours"].append(1.0)
            elif "SOIL" in stype or "MOISTURE" in stype:
                bucket["soil"].append(val)
            elif "PLUVIO" in stype or "RAIN" in stype or "PRECIP" in stype:
                bucket["precip"].append(val)

        results: list[dict[str, Any]] = []
        for farm_id, obs_date in sorted(all_keys):
            bucket = grouped.get((farm_id, obs_date))
            sat_nasa = nasa_idx.get((farm_id, obs_date), {})
            sat_meteo = meteo_idx.get((farm_id, obs_date), {})

            has_valid_iot_climate = bool(bucket and (bucket["temp"] or bucket["rh_vals"]))
            has_valid_iot_soil = bool(bucket and bucket["soil"])

            if has_valid_iot_climate and bucket is not None:
                temp_c = (
                    sum(bucket["temp"]) / len(bucket["temp"])
                    if bucket["temp"]
                    else float(sat_nasa.get("temp_c", sat_meteo.get("temp_c", 23.0)))
                )
                if bucket["rh_vals"]:
                    rh_hours = int(sum(bucket["rh_hours"]))
                elif "high_humidity_hours_gt85" in sat_meteo:
                    rh_hours = int(sat_meteo["high_humidity_hours_gt85"])
                elif sat_nasa.get("rh_pct", 0.0) > rh_threshold_pct:
                    rh_hours = 8
                else:
                    rh_hours = 0
                source_used = DataSourceUsed.IOT_SENSOR.value
            else:
                temp_c = float(sat_nasa.get("temp_c", sat_meteo.get("temp_c", 23.0)))
                if sat_meteo.get("high_humidity_hours_gt85", 0.0) > 0:
                    rh_hours = int(sat_meteo["high_humidity_hours_gt85"])
                elif sat_nasa.get("rh_pct", 0.0) > rh_threshold_pct:
                    rh_hours = 8
                else:
                    rh_hours = 0
                source_used = DataSourceUsed.SATELLITE_FALLBACK.value

            soil_pct = (
                sum(bucket["soil"]) / len(bucket["soil"])
                if (has_valid_iot_soil and bucket is not None)
                else 35.0
            )
            precip_mm = (
                sum(bucket["precip"])
                if (bucket and bucket["precip"])
                else float(sat_nasa.get("precip_mm", sat_meteo.get("precip_mm", 0.0)))
            )
            forecast_5d = float(sat_meteo.get("forecast_rain_next_5d_mm", 0.0))

            results.append(
                {
                    "farm_id": farm_id,
                    "observation_date": obs_date,
                    "high_humidity_hours_gt85": rh_hours,
                    "daily_mean_temp_c": round(temp_c, 2),
                    "soil_moisture_mean_pct": round(soil_pct, 2),
                    "daily_precip_mm": round(precip_mm, 2),
                    "forecast_rain_next_5d_mm": round(forecast_5d, 2),
                    "primary_source_used": source_used,
                }
            )

        return results


class PySparkRollingWindowAlertEngine:
    """Evaluates 4-day Coffee Rust (`RN-03`) and 5-day Water Deficit (`RN-04`, `CA-04`) windows."""

    def __init__(self, thresholds: RollingWindowThresholds | None = None) -> None:
        self.thresholds = thresholds or RollingWindowThresholds()

    def is_rust_conducive_day(
        self, high_humidity_hours_gt85: int, daily_mean_temp_c: float
    ) -> bool:
        """Return True if a daily observation satisfies RN-03 daily rust conditions."""
        t = self.thresholds
        return (
            high_humidity_hours_gt85 >= t.rust_min_hours_per_day
            and t.rust_temp_min_c <= daily_mean_temp_c <= t.rust_temp_max_c
        )

    def is_dry_soil_day(self, soil_moisture_mean_pct: float) -> bool:
        """Return True if a daily observation satisfies RN-04 dry soil condition (< 25%)."""
        return soil_moisture_mean_pct < self.thresholds.deficit_soil_moisture_max_pct

    @staticmethod
    def _is_consecutive_day(prev_date_str: str | None, curr_date_str: str) -> bool:
        if not prev_date_str:
            return False
        prev_d = date.fromisoformat(prev_date_str[:10])
        curr_d = date.fromisoformat(curr_date_str[:10])
        return (curr_d - prev_d).days == 1

    def evaluate_rolling_windows(
        self,
        daily_inputs: list[dict[str, Any]],
        generated_at_override: str | None = None,
    ) -> tuple[list[AgroclimaticDailyFeature], list[AgronomicAlertPayload]]:
        """Compute Silver daily features and trigger RN-03 / RN-04 / CA-04 alert payloads."""
        by_farm: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in daily_inputs:
            by_farm[str(row["farm_id"])].append(row)

        silver_features: list[AgroclimaticDailyFeature] = []
        alerts: list[AgronomicAlertPayload] = []
        t = self.thresholds

        for farm_id in sorted(by_farm.keys()):
            # Deduplicate by observation date keeping latest, ordered chronologically
            dedup_by_date: dict[str, dict[str, Any]] = {}
            for r in by_farm[farm_id]:
                obs_dt = str(r["observation_date"])[:10]
                dedup_by_date[obs_dt] = r

            ordered_rows = [dedup_by_date[d] for d in sorted(dedup_by_date.keys())]

            rust_streak = 0
            dry_streak = 0
            prev_date_str: str | None = None

            for idx, row in enumerate(ordered_rows):
                obs_date = str(row["observation_date"])[:10]
                rh_hours = int(row.get("high_humidity_hours_gt85", 0))
                temp_c = float(row.get("daily_mean_temp_c", 23.0))
                soil_pct = float(row.get("soil_moisture_mean_pct", 35.0))
                forecast_5d = float(row.get("forecast_rain_next_5d_mm", 0.0))
                source_used = str(row.get("primary_source_used") or DataSourceUsed.IOT_SENSOR.value)

                # 72-hour (3-day) rolling precipitation accumulation
                window_3d = ordered_rows[max(0, idx - 2) : idx + 1]
                precip_72h = round(
                    sum(
                        float(w.get("daily_precip_mm", w.get("precip_accum_72h_mm", 0.0)))
                        for w in window_3d
                    ),
                    2,
                )

                consecutive = self._is_consecutive_day(prev_date_str, obs_date)

                if self.is_rust_conducive_day(rh_hours, temp_c):
                    rust_streak = (rust_streak + 1) if consecutive else 1
                else:
                    rust_streak = 0

                if self.is_dry_soil_day(soil_pct):
                    dry_streak = (dry_streak + 1) if consecutive else 1
                else:
                    dry_streak = 0

                prev_date_str = obs_date

                feature = AgroclimaticDailyFeature(
                    farm_id=farm_id,
                    observation_date=obs_date,
                    high_humidity_hours_gt85=rh_hours,
                    daily_mean_temp_c=round(temp_c, 2),
                    soil_moisture_mean_pct=round(soil_pct, 2),
                    precip_accum_72h_mm=precip_72h,
                    forecast_rain_next_5d_mm=round(forecast_5d, 2),
                    consecutive_rust_days=rust_streak,
                    consecutive_dry_soil_days=dry_streak,
                    primary_source_used=source_used,
                )
                silver_features.append(feature)

                # Must be generated before 05:00 AM daily (RNF-02)
                gen_ts = generated_at_override or f"{obs_date}T04:30:00Z"

                # 1. Evaluate RN-03 / CA-03: 4-day Coffee Rust Rolling Window
                if rust_streak >= t.rust_window_days:
                    rust_window = ordered_rows[idx - t.rust_window_days + 1 : idx + 1]
                    avg_temp_4d = round(
                        sum(float(w.get("daily_mean_temp_c", temp_c)) for w in rust_window)
                        / len(rust_window),
                        2,
                    )
                    avg_rh_hours_4d = round(
                        sum(int(w.get("high_humidity_hours_gt85", rh_hours)) for w in rust_window)
                        / len(rust_window),
                        2,
                    )
                    sources_in_window = {
                        str(w.get("primary_source_used", source_used)) for w in rust_window
                    }
                    effective_source = (
                        DataSourceUsed.SATELLITE_FALLBACK
                        if DataSourceUsed.SATELLITE_FALLBACK.value in sources_in_window
                        else DataSourceUsed.IOT_SENSOR
                    )

                    evidence_core = {
                        "consecutive_days_hr_gt_85": rust_streak,
                        "avg_temp_c": avg_temp_4d,
                        "daily_hours_hr_gt_85": avg_rh_hours_4d,
                        "risk_level": "CRITICAL_EARLY_WARNING",
                        "rule_ref": "RN-03",
                        "observation_date": obs_date,
                    }
                    integrity_hash = AlertIntegrityHashValidator.compute_evidence_hash(
                        farm_id=farm_id,
                        alert_type=AlertType.COFFEE_RUST.value,
                        observation_date=obs_date,
                        evidence_core=evidence_core,
                    )
                    evidence_summary = {**evidence_core, "integrity_hash": integrity_hash}

                    alert_id = uuid5(
                        NAMESPACE_DNS,
                        f"ALERT:{AlertType.COFFEE_RUST.value}:{farm_id}:{obs_date}",
                    )
                    alerts.append(
                        AgronomicAlertPayload(
                            alert_id=alert_id,
                            farm_id=farm_id,
                            alert_type=AlertType.COFFEE_RUST,
                            weight_multiplier=t.rust_weight_multiplier,
                            data_source_used=effective_source,
                            forecast_days_ahead=t.forecast_days_ahead,
                            evidence_summary=evidence_summary,
                            alert_status=AlertStatus.ACTIVE,
                            generated_at=gen_ts,
                            observation_date=obs_date,
                        )
                    )

                # 2. Evaluate RN-04 / CA-04: 5-day Water Deficit Rolling Window
                if dry_streak >= t.deficit_window_days:
                    dry_window = ordered_rows[idx - t.deficit_window_days + 1 : idx + 1]
                    avg_moisture_5d = round(
                        sum(float(w.get("soil_moisture_mean_pct", soil_pct)) for w in dry_window)
                        / len(dry_window),
                        2,
                    )
                    sources_in_window = {
                        str(w.get("primary_source_used", source_used)) for w in dry_window
                    }
                    effective_source = (
                        DataSourceUsed.SATELLITE_FALLBACK
                        if DataSourceUsed.SATELLITE_FALLBACK.value in sources_in_window
                        else DataSourceUsed.IOT_SENSOR
                    )

                    if forecast_5d < t.deficit_forecast_rain_max_mm:
                        # Active Water Deficit Alert (RN-04)
                        evidence_core = {
                            "soil_moisture_pct": avg_moisture_5d,
                            "consecutive_dry_days": dry_streak,
                            "forecast_rain_mm": round(forecast_5d, 2),
                            "risk_level": "SEVERE_DEFICIT",
                            "rule_ref": "RN-04",
                            "observation_date": obs_date,
                        }
                        alert_status = AlertStatus.ACTIVE
                        natural_key = f"ALERT:{AlertType.WATER_DEFICIT.value}:{farm_id}:{obs_date}"
                    else:
                        # Suppressed / Cancelled due to imminent rainfall forecast >= 5mm (CA-04)
                        evidence_core = {
                            "soil_moisture_pct": avg_moisture_5d,
                            "consecutive_dry_days": dry_streak,
                            "forecast_rain_mm": round(forecast_5d, 2),
                            "action": "SUPPRESSED_DUE_TO_RAIN_FORECAST",
                            "rule_ref": "CA-04",
                            "observation_date": obs_date,
                        }
                        alert_status = AlertStatus.EXPIRED
                        natural_key = f"ALERT:DEFICIT_CANCEL:{farm_id}:{obs_date}"

                    integrity_hash = AlertIntegrityHashValidator.compute_evidence_hash(
                        farm_id=farm_id,
                        alert_type=AlertType.WATER_DEFICIT.value,
                        observation_date=obs_date,
                        evidence_core=evidence_core,
                    )
                    evidence_summary = {**evidence_core, "integrity_hash": integrity_hash}

                    alerts.append(
                        AgronomicAlertPayload(
                            alert_id=uuid5(NAMESPACE_DNS, natural_key),
                            farm_id=farm_id,
                            alert_type=AlertType.WATER_DEFICIT,
                            weight_multiplier=t.deficit_weight_multiplier,
                            data_source_used=effective_source,
                            forecast_days_ahead=t.forecast_days_ahead,
                            evidence_summary=evidence_summary,
                            alert_status=alert_status,
                            generated_at=gen_ts,
                            observation_date=obs_date,
                        )
                    )

        return silver_features, alerts

    def build_pyspark_window_dataframe(self, spark_df: Any) -> Any:
        """Apply native PySpark `Window.partitionBy('farm_id').orderBy(...)` (SCRUM-165).

        Used when running on an Apache Spark cluster (ECS Fargate / EMR). Evaluates 4-day
        (`rowsBetween(-3, 0)`) and 5-day (`rowsBetween(-4, 0)`) sliding windows over `spark_df`.
        """
        from pyspark.sql import Window  # type: ignore
        from pyspark.sql import functions as F  # type: ignore

        t = self.thresholds
        w_4d = Window.partitionBy("farm_id").orderBy("observation_date").rowsBetween(-3, 0)
        w_5d = Window.partitionBy("farm_id").orderBy("observation_date").rowsBetween(-4, 0)
        w_3d = Window.partitionBy("farm_id").orderBy("observation_date").rowsBetween(-2, 0)

        is_rust_day = F.when(
            (F.col("high_humidity_hours_gt85") >= t.rust_min_hours_per_day)
            & (F.col("daily_mean_temp_c") >= t.rust_temp_min_c)
            & (F.col("daily_mean_temp_c") <= t.rust_temp_max_c),
            F.lit(1),
        ).otherwise(F.lit(0))

        is_dry_day = F.when(
            F.col("soil_moisture_mean_pct") < t.deficit_soil_moisture_max_pct,
            F.lit(1),
        ).otherwise(F.lit(0))

        return (
            spark_df.withColumn("is_rust_day", is_rust_day)
            .withColumn("is_dry_day", is_dry_day)
            .withColumn("consecutive_rust_days", F.sum("is_rust_day").over(w_4d))
            .withColumn("consecutive_dry_soil_days", F.sum("is_dry_day").over(w_5d))
            .withColumn("precip_accum_72h_mm", F.round(F.sum("daily_precip_mm").over(w_3d), 2))
            .withColumn(
                "trigger_coffee_rust",
                F.col("consecutive_rust_days") >= F.lit(t.rust_window_days),
            )
            .withColumn(
                "trigger_water_deficit",
                (F.col("consecutive_dry_soil_days") >= F.lit(t.deficit_window_days))
                & (F.col("forecast_rain_next_5d_mm") < F.lit(t.deficit_forecast_rain_max_mm)),
            )
            .withColumn(
                "suppress_water_deficit_ca04",
                (F.col("consecutive_dry_soil_days") >= F.lit(t.deficit_window_days))
                & (F.col("forecast_rain_next_5d_mm") >= F.lit(t.deficit_forecast_rain_max_mm)),
            )
        )


class AgroclimaticAlertOutputWriter:
    """Writes Silver agroclimatic features and alerts to S3 Parquet and PostgreSQL (SCRUM-169)."""

    SILVER_FEATURES_SCHEMA = pa.schema(
        [
            pa.field("farm_id", pa.string(), nullable=False),
            pa.field("observation_date", pa.string(), nullable=False),
            pa.field("high_humidity_hours_gt85", pa.int32(), nullable=False),
            pa.field("daily_mean_temp_c", pa.float64(), nullable=False),
            pa.field("soil_moisture_mean_pct", pa.float64(), nullable=False),
            pa.field("precip_accum_72h_mm", pa.float64(), nullable=False),
            pa.field("forecast_rain_next_5d_mm", pa.float64(), nullable=False),
            pa.field("consecutive_rust_days", pa.int32(), nullable=False),
            pa.field("consecutive_dry_soil_days", pa.int32(), nullable=False),
            pa.field("primary_source_used", pa.string(), nullable=False),
        ]
    )

    ALERTS_PARQUET_SCHEMA = pa.schema(
        [
            pa.field("alert_id", pa.string(), nullable=False),
            pa.field("farm_id", pa.string(), nullable=False),
            pa.field("alert_type", pa.string(), nullable=False),
            pa.field("weight_multiplier", pa.int32(), nullable=False),
            pa.field("data_source_used", pa.string(), nullable=False),
            pa.field("forecast_days_ahead", pa.int32(), nullable=False),
            pa.field("evidence_summary_json", pa.string(), nullable=False),
            pa.field("alert_status", pa.string(), nullable=False),
            pa.field("generated_at", pa.string(), nullable=False),
            pa.field("observation_date", pa.string(), nullable=False),
        ]
    )

    @classmethod
    def features_to_snappy_parquet_bytes(
        cls,
        features: list[AgroclimaticDailyFeature],
    ) -> bytes:
        rows = [f.to_dict() for f in features]
        table = pa.Table.from_pylist(rows, schema=cls.SILVER_FEATURES_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="SNAPPY", use_dictionary=True)
        return buf.getvalue()

    @classmethod
    def alerts_to_snappy_parquet_bytes(
        cls,
        alerts: list[AgronomicAlertPayload],
    ) -> bytes:
        rows = [
            {
                "alert_id": str(a.alert_id),
                "farm_id": a.farm_id,
                "alert_type": a.alert_type.value,
                "weight_multiplier": int(a.weight_multiplier),
                "data_source_used": a.data_source_used.value,
                "forecast_days_ahead": int(a.forecast_days_ahead),
                "evidence_summary_json": json.dumps(a.evidence_summary, sort_keys=True),
                "alert_status": a.alert_status.value,
                "generated_at": a.generated_at,
                "observation_date": a.observation_date,
            }
            for a in alerts
        ]
        table = pa.Table.from_pylist(rows, schema=cls.ALERTS_PARQUET_SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="SNAPPY", use_dictionary=True)
        return buf.getvalue()

    @classmethod
    def write_features_and_alerts_to_s3(
        cls,
        s3_client: Any,
        bucket_name: str,
        features: list[AgroclimaticDailyFeature],
        alerts: list[AgronomicAlertPayload],
        glue_client: Any | None = None,
        database_name: str = "agrosense_datalake",
    ) -> dict[str, list[str]]:
        """Upload Snappy Parquet partitions by `dt=YYYY-MM-DD` to S3 and register in Glue."""
        features_by_dt: dict[str, list[AgroclimaticDailyFeature]] = defaultdict(list)
        for f in features:
            features_by_dt[f.observation_date].append(f)

        alerts_by_dt: dict[str, list[AgronomicAlertPayload]] = defaultdict(list)
        for a in alerts:
            alerts_by_dt[a.observation_date].append(a)

        feature_uris: list[str] = []
        alert_uris: list[str] = []

        catalog = (
            GlueDataCatalogManager(database_name=database_name, bucket_name=bucket_name)
            if glue_client is not None
            else None
        )

        for dt_str, dt_features in sorted(features_by_dt.items()):
            parquet_bytes = cls.features_to_snappy_parquet_bytes(dt_features)
            s3_key = f"trusted/agroclimatic_daily_features/dt={dt_str}/part-00000.snappy.parquet"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=parquet_bytes,
                ContentType="application/vnd.apache.parquet",
            )
            feature_uris.append(f"s3://{bucket_name}/{s3_key}")
            if catalog is not None and glue_client is not None:
                catalog.register_partition(
                    glue_client=glue_client,
                    table_name="trusted_agroclimatic_daily_features",
                    partition_date=dt_str,
                )

        for dt_str, dt_alerts in sorted(alerts_by_dt.items()):
            parquet_bytes = cls.alerts_to_snappy_parquet_bytes(dt_alerts)
            s3_key = f"refined/agronomic_alerts/dt={dt_str}/part-00000.snappy.parquet"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=parquet_bytes,
                ContentType="application/vnd.apache.parquet",
            )
            alert_uris.append(f"s3://{bucket_name}/{s3_key}")

        return {
            "feature_parquet_uris": feature_uris,
            "alert_parquet_uris": alert_uris,
        }

    @staticmethod
    async def upsert_alerts_to_postgres(
        db_session: AsyncSession,
        alerts: list[AgronomicAlertPayload],
    ) -> int:
        """Idempotently upsert generated alerts into PostgreSQL `agronomic_alerts` (SCRUM-169)."""
        if not alerts:
            return 0

        stmt = text(
            """
            INSERT INTO agronomic_alerts (
                alert_id,
                farm_id,
                alert_type,
                weight_multiplier,
                data_source_used,
                forecast_days_ahead,
                evidence_summary,
                alert_status,
                generated_at
            ) VALUES (
                :alert_id,
                :farm_id,
                :alert_type,
                :weight_multiplier,
                :data_source_used,
                :forecast_days_ahead,
                CAST(:evidence_summary AS JSONB),
                :alert_status,
                :generated_at
            )
            ON CONFLICT (alert_id) DO UPDATE SET
                alert_status = EXCLUDED.alert_status,
                data_source_used = EXCLUDED.data_source_used,
                evidence_summary = EXCLUDED.evidence_summary,
                generated_at = EXCLUDED.generated_at
            """
        )

        for alert in alerts:
            gen_dt = datetime.fromisoformat(alert.generated_at.replace("Z", "+00:00")).astimezone(
                UTC
            )
            await db_session.execute(
                stmt,
                {
                    "alert_id": str(alert.alert_id),
                    "farm_id": str(alert.farm_id),
                    "alert_type": alert.alert_type.value,
                    "weight_multiplier": int(alert.weight_multiplier),
                    "data_source_used": alert.data_source_used.value,
                    "forecast_days_ahead": int(alert.forecast_days_ahead),
                    "evidence_summary": json.dumps(alert.evidence_summary, sort_keys=True),
                    "alert_status": alert.alert_status.value,
                    "generated_at": gen_dt,
                },
            )
        await db_session.commit()
        return len(alerts)
