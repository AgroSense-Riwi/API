"""Custom Operator and Engine for NASA POWER Daily Point API Ingestion (Fuente F2).

Complies with:
- Endpoint: NASA POWER Daily Point API
- Variables: PRECTOTCORR, T2M_MEAN, ALLSKY_SFC_SW_DWN, RH2M
- Quota management, throttling & exponential backoff (RNF-05)
- Local disk caching for intermediate failure resilience
- Idempotent S3 Bronze persistence:
  s3://${S3_BUCKET_BRONZE}/weather_nasa_power/dt={{ ds }}/nasa_power_{{ ds }}.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import requests

try:
    from airflow.models import BaseOperator  # type: ignore

    HAS_AIRFLOW = True
except ImportError:

    class BaseOperator:  # type: ignore
        """Fallback BaseOperator for non-Airflow runtime environments."""

        template_fields: tuple[str, ...] = ()

        def __init__(self, task_id: str, **kwargs: Any) -> None:
            self.task_id = task_id
            self.kwargs = kwargs

        def execute(self, context: dict[str, Any]) -> Any:
            raise NotImplementedError

    HAS_AIRFLOW = False

# Import S3 Bronze Writer
try:
    from utils.s3_bronze_writer import S3BronzeWriter
except ImportError:
    from pipelines.airflow.plugins.utils.s3_bronze_writer import S3BronzeWriter

logger = logging.getLogger(__name__)

NASA_POWER_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
DEFAULT_PARAMETERS = ["PRECTOTCORR", "T2M_MEAN", "ALLSKY_SFC_SW_DWN", "RH2M"]


def fetch_single_point_nasa_power(
    latitude: float,
    longitude: float,
    date_str: str,
    parameters: list[str] | None = None,
    session: requests.Session | None = None,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    backoff_factor: float = 2.0,
    timeout: int = 20,
) -> dict[str, Any]:
    """Fetch daily climate parameters for a single lat/lon point from NASA POWER.

    Args:
        latitude: Farm latitude coordinate.
        longitude: Farm longitude coordinate.
        date_str: Logical execution date in YYYY-MM-DD or YYYYMMDD format.
        parameters: Climatic parameter codes list.
        session: Reusable HTTP requests session.
        max_retries: Number of attempts on transient failure.
        initial_backoff: Initial seconds before retry.
        backoff_factor: Multiplier for exponential backoff.
        timeout: HTTP request timeout in seconds.

    Returns:
        JSON response payload from NASA POWER.
    """
    clean_date = date_str.replace("-", "")
    params_list = parameters or DEFAULT_PARAMETERS
    http = session or requests.Session()

    query_params = {
        "parameters": ",".join(params_list),
        "community": "AG",
        "longitude": round(float(longitude), 4),
        "latitude": round(float(latitude), 4),
        "start": clean_date,
        "end": clean_date,
        "format": "JSON",
    }

    current_backoff = initial_backoff
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = http.get(NASA_POWER_ENDPOINT, params=query_params, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    "NASA POWER API returned HTTP %s for (%s, %s). "
                    "Attempt %s/%s. Retrying in %.2fs",
                    response.status_code,
                    latitude,
                    longitude,
                    attempt,
                    max_retries,
                    current_backoff,
                )
                time.sleep(current_backoff)
                current_backoff *= backoff_factor
                continue
            else:
                response.raise_for_status()
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
            logger.warning(
                "Request error querying NASA POWER for (%s, %s): %s. "
                "Attempt %s/%s. Retrying in %.2fs",
                latitude,
                longitude,
                exc,
                attempt,
                max_retries,
                current_backoff,
            )
            time.sleep(current_backoff)
            current_backoff *= backoff_factor

    err_msg = (
        f"Failed to fetch NASA POWER data for ({latitude}, {longitude}) "
        f"after {max_retries} attempts: {last_error}"
    )
    raise RuntimeError(err_msg)


def extract_nasa_power_data(
    ds: str,
    farms: list[dict[str, Any]],
    cache_dir: str | None = None,
    rate_limit_delay_sec: float = 0.05,
    max_retries: int = 3,
    bucket_name: str | None = None,
    s3_writer: S3BronzeWriter | None = None,
) -> dict[str, Any]:
    """Extract NASA POWER daily weather metrics for a collection of pilot farms.

    Implements:
    - Local disk caching to avoid redundant HTTP calls on task retries.
    - Throttling/rate limiting between calls.
    - Batch persistence to Bronze S3 layer.
    """
    clean_ds = ds.strip()
    effective_cache_dir = cache_dir or os.getenv(
        "AIRFLOW_CACHE_DIR",
        os.path.join("/tmp", "airflow_cache"),  # noqa: S108
    )
    daily_cache_path = os.path.join(effective_cache_dir, "nasa_power", clean_ds)
    os.makedirs(daily_cache_path, exist_ok=True)

    writer = s3_writer or S3BronzeWriter(bucket_name=bucket_name)
    session = requests.Session()

    records: list[dict[str, Any]] = []
    successful_count = 0
    failed_count = 0

    logger.info("Starting NASA POWER ingestion for %d farms on date %s", len(farms), clean_ds)

    for idx, farm in enumerate(farms):
        farm_id = str(farm.get("farm_id") or f"farm_{idx}")
        farm_code = str(farm.get("farm_code") or f"FARM-{idx + 1:03d}")
        lat = float(farm["latitude"])
        lon = float(farm["longitude"])

        cache_filename = f"{farm_code}_{lat:.4f}_{lon:.4f}.json"
        cache_file = os.path.join(daily_cache_path, cache_filename)

        farm_data: dict[str, Any] | None = None

        # Check local cache first
        if os.path.exists(cache_file):
            try:
                with open(cache_file, encoding="utf-8") as f:
                    farm_data = json.load(f)
                logger.debug("Cache hit for farm %s at %s", farm_code, cache_file)
            except Exception as e:
                logger.warning("Corrupt cache for %s, re-fetching: %s", farm_code, e)

        if farm_data is None:
            try:
                raw_response = fetch_single_point_nasa_power(
                    latitude=lat,
                    longitude=lon,
                    date_str=clean_ds,
                    session=session,
                    max_retries=max_retries,
                )
                farm_data = raw_response

                # Persist to local cache for resume resilience
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(farm_data, f, ensure_ascii=False)

                # Rate limiting throttle
                if rate_limit_delay_sec > 0:
                    time.sleep(rate_limit_delay_sec)

            except Exception as exc:
                failed_count += 1
                logger.error(
                    "Failed NASA POWER extraction for farm %s (%s, %s): %s",
                    farm_code,
                    lat,
                    lon,
                    exc,
                )
                records.append(
                    {
                        "farm_id": farm_id,
                        "farm_code": farm_code,
                        "latitude": lat,
                        "longitude": lon,
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )
                continue

        successful_count += 1
        records.append(
            {
                "farm_id": farm_id,
                "farm_code": farm_code,
                "latitude": lat,
                "longitude": lon,
                "status": "SUCCESS",
                "data": farm_data,
            }
        )

    consolidated_payload = {
        "source": "NASA_POWER",
        "community": "AG",
        "parameters": DEFAULT_PARAMETERS,
        "execution_date": clean_ds,
        "ingestion_timestamp": datetime.now(UTC).isoformat(),
        "total_farms": len(farms),
        "successful_count": successful_count,
        "failed_count": failed_count,
        "records": records,
    }

    s3_key = S3BronzeWriter.get_nasa_power_s3_key(clean_ds)
    s3_uri = writer.write_json(s3_key=s3_key, payload=consolidated_payload, replace=True)

    logger.info(
        "NASA POWER extraction completed: %d success, %d failed. Output stored at %s",
        successful_count,
        failed_count,
        s3_uri,
    )

    return {
        "s3_uri": s3_uri,
        "s3_key": s3_key,
        "total_farms": len(farms),
        "successful_count": successful_count,
        "failed_count": failed_count,
    }


class NasaPowerIngestionOperator(BaseOperator):
    """Airflow Operator for NASA POWER daily agro-climatic ingestion."""

    template_fields: tuple[str, ...] = ("ds",)

    def __init__(
        self,
        task_id: str = "extract_nasa_power_weather",
        farms_task_id: str = "fetch_pilot_farm_coordinates",
        bucket_name: str | None = None,
        rate_limit_delay_sec: float = 0.05,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(task_id=task_id, **kwargs)
        self.farms_task_id = farms_task_id
        self.bucket_name = bucket_name
        self.rate_limit_delay_sec = rate_limit_delay_sec
        self.max_retries = max_retries

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute NASA POWER ingestion task."""
        ds = context.get("ds") or datetime.now(UTC).strftime("%Y-%m-%d")
        ti = context.get("ti")

        farms: list[dict[str, Any]] = []
        if ti is not None:
            farms = ti.xcom_pull(task_ids=self.farms_task_id) or []

        if not farms:
            logger.warning(
                "No farms received from task '%s'. Checking context parameters.", self.farms_task_id
            )
            farms = context.get("farms", [])

        if not farms:
            raise ValueError(f"Farms list is empty for NASA POWER ingestion on date {ds}")

        return extract_nasa_power_data(
            ds=ds,
            farms=farms,
            rate_limit_delay_sec=self.rate_limit_delay_sec,
            max_retries=self.max_retries,
            bucket_name=self.bucket_name,
        )
