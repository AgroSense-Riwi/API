"""Unit and Integration Tests for DAG Satellite Ingestion (User Story AGRO-18).

Covers:
- DAG syntax, SLA (3h RNF-02 / CA-10), schedule (02:00 UTC), retries (exponential backoff).
- Pilot farm coordinates extraction (300 farms, is_pilot_sensor_farm = TRUE).
- NASA POWER API (Fuente F2) contracts, variables, retries & throttling.
- Open-Meteo Forecast API (Fuente F3) contracts, 7-day parameters.
- Local disk caching to prevent quota exhaustion and ensure fault tolerance.
- Idempotent Bronze S3 partitioning (CT-08).
- Downstream agronomic alert trigger (RN-03, RN-04, RN-05).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests_mock

# Ensure pipelines and plugins are in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PIPELINES_DIR = os.path.join(BASE_DIR, "pipelines", "airflow")
PLUGINS_DIR = os.path.join(PIPELINES_DIR, "plugins")

for path in [PIPELINES_DIR, PLUGINS_DIR, os.path.join(PIPELINES_DIR, "dags")]:
    if path not in sys.path:
        sys.path.insert(0, path)

from dag_satellite_ingestion import (  # noqa: E402
    PILOT_FARMS_QUERY,
    dag,
    fetch_pilot_farm_coordinates,
    trigger_downstream_alert_engine,
)
from operators.nasa_power_operator import (  # noqa: E402
    NASA_POWER_ENDPOINT,
    extract_nasa_power_data,
    fetch_single_point_nasa_power,
)
from operators.open_meteo_operator import (  # noqa: E402
    OPEN_METEO_ENDPOINT,
    extract_open_meteo_data,
    fetch_single_point_open_meteo,
)
from utils.s3_bronze_writer import S3BronzeWriter  # noqa: E402


class TestDagSatelliteIngestionSyntaxAndSLA:
    """Validate DAG structural integrity, SLA and execution parameters."""

    def test_dag_id_and_schedule(self) -> None:
        """DAG ID must match naming convention and schedule at 02:00 AM UTC."""
        assert dag.dag_id == "dag_satellite_ingestion"
        schedule = getattr(dag, "schedule", getattr(dag, "schedule_interval", None))
        assert schedule == "0 2 * * *"
        assert dag.catchup is False

    def test_sla_and_retry_policies(self) -> None:
        """SLA must enforce 3-hour window (RNF-02, CA-10) with 3 exponential backoff retries."""
        default_args = dag.default_args
        assert default_args.get("sla") == timedelta(hours=3)
        assert default_args.get("retries") == 3
        assert default_args.get("retry_delay") == timedelta(minutes=5)
        assert default_args.get("retry_exponential_backoff") is True

    def test_dag_tasks_and_dependencies(self) -> None:
        """Verify the 4 required tasks and upstream/downstream flow."""
        task_dict = {t.task_id: t for t in dag.tasks}
        expected_tasks = {
            "fetch_pilot_farm_coordinates",
            "extract_nasa_power_weather",
            "extract_open_meteo_forecast",
            "trigger_downstream_alert_engine",
        }
        assert expected_tasks.issubset(set(task_dict.keys()))

        t_fetch = task_dict["fetch_pilot_farm_coordinates"]
        t_nasa = task_dict["extract_nasa_power_weather"]
        t_meteo = task_dict["extract_open_meteo_forecast"]
        t_trigger = task_dict["trigger_downstream_alert_engine"]

        # Check dependencies
        assert t_nasa in t_fetch.downstream_list
        assert t_meteo in t_fetch.downstream_list
        assert t_trigger in t_nasa.downstream_list
        assert t_trigger in t_meteo.downstream_list


class TestPilotFarmCoordinates:
    """Validate retrieval and filtering of the 300 pilot farms."""

    def test_sql_query_structure(self) -> None:
        """SQL query must target active pilot sensor farms."""
        assert "is_pilot_sensor_farm = TRUE" in PILOT_FARMS_QUERY
        assert "is_active = TRUE" in PILOT_FARMS_QUERY
        assert "latitude" in PILOT_FARMS_QUERY
        assert "longitude" in PILOT_FARMS_QUERY

    def test_fetch_pilot_farms_deterministic_fallback(self) -> None:
        """Without active DB connection, deterministic fallback must yield 300 pilot farms."""
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            farms = fetch_pilot_farm_coordinates()
            assert len(farms) == 300
            for farm in farms:
                assert "farm_id" in farm
                assert "farm_code" in farm
                assert "latitude" in farm
                assert "longitude" in farm
                assert isinstance(farm["latitude"], float)
                assert isinstance(farm["longitude"], float)

    def test_fetch_pilot_farms_with_mock_db(self) -> None:
        """When DB is available, rows must be transformed into coordinates list."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("f-001", "FARM-001", "Hacienda El Cafeto", 4.812345, -75.698765),
            ("f-002", "FARM-002", "Finca La Esperanza", 4.851234, -75.621234),
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("psycopg.connect", return_value=mock_conn):
            with patch.dict(
                os.environ, {"DATABASE_URL": "postgresql://test:test@localhost:5432/test"}
            ):
                farms = fetch_pilot_farm_coordinates()
                assert len(farms) == 2
                assert farms[0]["farm_code"] == "FARM-001"
                assert farms[0]["latitude"] == 4.812345
                assert farms[0]["longitude"] == -75.698765


class TestNasaPowerIngestion:
    """Validate NASA POWER API (Fuente F2) ingestion, contracts and caching."""

    def test_fetch_single_point_contract(self) -> None:
        """NASA POWER parameters PRECTOTCORR, T2M_MEAN, ALLSKY_SFC_SW_DWN, RH2M must be queried."""
        with requests_mock.Mocker() as m:
            mock_payload = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-75.6961, 4.8133, 1500.0]},
                "properties": {
                    "parameter": {
                        "PRECTOTCORR": {"20261001": 12.4},
                        "T2M_MEAN": {"20261001": 19.8},
                        "ALLSKY_SFC_SW_DWN": {"20261001": 18.2},
                        "RH2M": {"20261001": 84.5},
                    }
                },
            }
            m.get(NASA_POWER_ENDPOINT, json=mock_payload)

            result = fetch_single_point_nasa_power(
                latitude=4.8133,
                longitude=-75.6961,
                date_str="2026-10-01",
            )

            assert m.called
            last_request = m.last_request
            params_requested = last_request.qs["parameters"][0].upper()
            assert "PRECTOTCORR" in params_requested
            assert "T2M_MEAN" in params_requested
            assert "ALLSKY_SFC_SW_DWN" in params_requested
            assert "RH2M" in params_requested
            assert last_request.qs["community"][0].upper() == "AG"
            assert last_request.qs["start"] == ["20261001"]
            assert last_request.qs["end"] == ["20261001"]
            assert result["properties"]["parameter"]["PRECTOTCORR"]["20261001"] == 12.4

    def test_nasa_power_retry_on_http_429(self) -> None:
        """NASA POWER must retry upon HTTP 429 and succeed on subsequent attempt."""
        with requests_mock.Mocker() as m:
            m.register_uri(
                "GET",
                NASA_POWER_ENDPOINT,
                [
                    {"status_code": 429, "text": "Too Many Requests"},
                    {
                        "status_code": 200,
                        "json": {"properties": {"parameter": {"PRECTOTCORR": {"20261001": 5.0}}}},
                    },
                ],
            )
            result = fetch_single_point_nasa_power(
                latitude=4.8133,
                longitude=-75.6961,
                date_str="2026-10-01",
                initial_backoff=0.01,
            )
            assert m.call_count == 2
            assert result["properties"]["parameter"]["PRECTOTCORR"]["20261001"] == 5.0

    def test_nasa_power_local_caching(self) -> None:
        """Cached API response on disk must avoid secondary external HTTP requests."""
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_farms = [
                {"farm_id": "f-1", "farm_code": "FARM-001", "latitude": 4.81, "longitude": -75.69}
            ]
            with requests_mock.Mocker() as m:
                m.get(
                    NASA_POWER_ENDPOINT,
                    json={"properties": {"parameter": {"PRECTOTCORR": {"20261001": 8.0}}}},
                )

                # Call 1: HTTP call made
                res1 = extract_nasa_power_data(
                    ds="2026-10-01",
                    farms=sample_farms,
                    cache_dir=temp_dir,
                    rate_limit_delay_sec=0.0,
                )
                assert m.call_count == 1
                assert res1["successful_count"] == 1

                # Call 2: Uses disk cache, 0 new HTTP calls
                res2 = extract_nasa_power_data(
                    ds="2026-10-01",
                    farms=sample_farms,
                    cache_dir=temp_dir,
                    rate_limit_delay_sec=0.0,
                )
                assert m.call_count == 1  # Unchanged!
                assert res2["successful_count"] == 1


class TestOpenMeteoIngestion:
    """Validate Open-Meteo Forecast API (Fuente F3) ingestion and 7-day contract."""

    def test_fetch_single_point_contract(self) -> None:
        """Open-Meteo parameters precipitation_sum, temperature_2m_mean, relative_humidity_2m."""
        with requests_mock.Mocker() as m:
            mock_payload = {
                "latitude": 4.81,
                "longitude": -75.69,
                "daily": {
                    "time": [
                        "2026-10-01",
                        "2026-10-02",
                        "2026-10-03",
                        "2026-10-04",
                        "2026-10-05",
                        "2026-10-06",
                        "2026-10-07",
                    ],
                    "precipitation_sum": [10.2, 5.4, 0.0, 1.2, 15.0, 8.2, 2.1],
                    "temperature_2m_mean": [20.1, 19.5, 21.0, 20.8, 18.9, 19.4, 20.2],
                },
                "hourly": {
                    "time": ["2026-10-01T00:00", "2026-10-01T01:00"],
                    "relative_humidity_2m": [88, 89],
                },
            }
            m.get(OPEN_METEO_ENDPOINT, json=mock_payload)

            result = fetch_single_point_open_meteo(
                latitude=4.81,
                longitude=-75.69,
                forecast_days=7,
            )

            assert m.called
            last_request = m.last_request
            daily_params = last_request.qs["daily"]
            assert "precipitation_sum" in daily_params
            assert "temperature_2m_mean" in daily_params
            assert "relative_humidity_2m" in last_request.qs["hourly"]
            assert last_request.qs["forecast_days"] == ["7"]
            assert len(result["daily"]["precipitation_sum"]) == 7

    def test_open_meteo_caching_and_batch_persistence(self) -> None:
        """Verify Open-Meteo caching and S3 Bronze path formatting."""
        with tempfile.TemporaryDirectory() as temp_dir:
            sample_farms = [
                {"farm_id": "f-1", "farm_code": "FARM-001", "latitude": 4.81, "longitude": -75.69},
                {"farm_id": "f-2", "farm_code": "FARM-002", "latitude": 4.85, "longitude": -75.62},
            ]
            with requests_mock.Mocker() as m:
                m.get(
                    OPEN_METEO_ENDPOINT,
                    json={
                        "daily": {
                            "precipitation_sum": [0.0] * 7,
                            "temperature_2m_mean": [20.0] * 7,
                        },
                        "hourly": {"relative_humidity_2m": [80] * 24},
                    },
                )

                result = extract_open_meteo_data(
                    ds="2026-10-01",
                    farms=sample_farms,
                    forecast_days=7,
                    cache_dir=temp_dir,
                    rate_limit_delay_sec=0.0,
                )

                assert result["successful_count"] == 2
                assert result["failed_count"] == 0
                assert (
                    result["s3_key"]
                    == "weather_open_meteo/dt=2026-10-01/open_meteo_2026-10-01.json"
                )


class TestS3BronzeWriterAndIdempotency:
    """Validate S3 Medallion Bronze partition schema and backfill idempotency (CT-08)."""

    def test_s3_partition_keys_format(self) -> None:
        """Partition paths must strictly match dt=YYYY-MM-DD schema."""
        ds = "2026-10-01"
        nasa_key = S3BronzeWriter.get_nasa_power_s3_key(ds)
        meteo_key = S3BronzeWriter.get_open_meteo_s3_key(ds)

        assert nasa_key == "weather_nasa_power/dt=2026-10-01/nasa_power_2026-10-01.json"
        assert meteo_key == "weather_open_meteo/dt=2026-10-01/open_meteo_2026-10-01.json"

    def test_idempotent_write_overwrite(self) -> None:
        """Writing to the same partition twice overwrites atomically without corrupting data."""
        writer = S3BronzeWriter(bucket_name="test-agrosense-bronze")
        mock_s3 = MagicMock()
        writer._s3_client = mock_s3

        s3_key = S3BronzeWriter.get_nasa_power_s3_key("2026-10-01")
        payload1 = {"execution_date": "2026-10-01", "run": 1}
        payload2 = {"execution_date": "2026-10-01", "run": 2}

        uri1 = writer.write_json(s3_key=s3_key, payload=payload1, replace=True)
        uri2 = writer.write_json(s3_key=s3_key, payload=payload2, replace=True)

        assert (
            uri1
            == uri2
            == "s3://test-agrosense-bronze/weather_nasa_power/dt=2026-10-01/nasa_power_2026-10-01.json"
        )
        assert mock_s3.put_object.call_count == 2
        # Check last put body
        last_body = json.loads(mock_s3.put_object.call_args[1]["Body"].decode("utf-8"))
        assert last_body["run"] == 2


class TestDownstreamAlertTrigger:
    """Validate completion callback and downstream alerting trigger."""

    def test_trigger_downstream_alert_engine_payload(self) -> None:
        """Downstream trigger must report readiness with partition URIs."""
        mock_ti = MagicMock()
        mock_ti.xcom_pull.side_effect = [
            {"s3_uri": "s3://bronze/weather_nasa_power/dt=2026-10-01/nasa_power_2026-10-01.json"},
            {"s3_uri": "s3://bronze/weather_open_meteo/dt=2026-10-01/open_meteo_2026-10-01.json"},
        ]

        context = {"ds": "2026-10-01", "ti": mock_ti}
        result = trigger_downstream_alert_engine(**context)

        assert result["status"] == "COMPLETED"
        assert result["ds"] == "2026-10-01"
        assert result["downstream_ready"] is True
        assert "weather_nasa_power" in result["nasa_power_s3"]
        assert "weather_open_meteo" in result["open_meteo_s3"]
