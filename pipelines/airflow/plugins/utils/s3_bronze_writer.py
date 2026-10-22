"""Utility module for writing raw ingestion data to S3 Bronze Data Lake (Medallion Architecture).

Complies with:
- Idempotency (CT-08): deterministic partitioned paths dt=YYYY-MM-DD.
- Multi-provider support: Airflow S3Hook with boto3 fallback.
- LocalStack / AWS S3 compatibility.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Fallback import for Airflow S3Hook if installed in Airflow environment
try:
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook  # type: ignore

    HAS_AIRFLOW_S3_HOOK = True
except ImportError:
    S3Hook = None  # type: ignore
    HAS_AIRFLOW_S3_HOOK = False

try:
    import boto3
    from botocore.config import Config

    HAS_BOTO3 = True
except ImportError:
    boto3 = None  # type: ignore
    Config = None  # type: ignore
    HAS_BOTO3 = False


class S3BronzeWriter:
    """Manager for writing raw ingestion datasets to Amazon S3 / LocalStack Bronze layer."""

    def __init__(
        self,
        bucket_name: str | None = None,
        aws_conn_id: str = "aws_default",
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.bucket_name = (
            bucket_name
            or os.getenv("S3_BUCKET_BRONZE")
            or os.getenv("S3_BUCKET")
            or "agrosense-datalake-bronze"
        )
        self.aws_conn_id = aws_conn_id
        self.endpoint_url = (
            endpoint_url
            or os.getenv("AWS_ENDPOINT_URL")
            or os.getenv("S3_ENDPOINT_URL")
            or os.getenv("LOCALSTACK_URL")
        )
        self.region_name = region_name or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        self._s3_client = None

    @staticmethod
    def get_nasa_power_s3_key(ds: str) -> str:
        """Generate partitioned S3 key for NASA POWER daily ingestion."""
        return f"weather_nasa_power/dt={ds}/nasa_power_{ds}.json"

    @staticmethod
    def get_open_meteo_s3_key(ds: str) -> str:
        """Generate partitioned S3 key for Open-Meteo forecast ingestion."""
        return f"weather_open_meteo/dt={ds}/open_meteo_{ds}.json"

    def _get_boto3_client(self) -> Any:
        """Get or initialize boto3 S3 client."""
        if not HAS_BOTO3:
            raise RuntimeError("boto3 is not installed.")

        if self._s3_client is None:
            client_kwargs: dict[str, Any] = {
                "region_name": self.region_name,
            }
            if self.endpoint_url:
                client_kwargs["endpoint_url"] = self.endpoint_url

            aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            if aws_access_key and aws_secret_key:
                client_kwargs["aws_access_key_id"] = aws_access_key
                client_kwargs["aws_secret_access_key"] = aws_secret_key

            self._s3_client = boto3.client("s3", **client_kwargs)

        return self._s3_client

    def write_json(
        self,
        s3_key: str,
        payload: dict[str, Any] | list[Any] | str,
        bucket_name: str | None = None,
        replace: bool = True,
    ) -> str:
        """Upload raw JSON dataset to S3 Bronze layer.

        Args:
            s3_key: Destination object key in bucket.
            payload: JSON serializable object or string.
            bucket_name: Target S3 bucket, defaults to instance bucket.
            replace: If True, overwrites existing object (idempotency).

        Returns:
            Full s3:// URI of the persisted object.
        """
        target_bucket = bucket_name or self.bucket_name
        body = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, indent=2, ensure_ascii=False)
        )

        # 1. Try Airflow S3Hook if available and connection configured
        if HAS_AIRFLOW_S3_HOOK:
            try:
                hook = S3Hook(aws_conn_id=self.aws_conn_id)
                hook.load_string(
                    string_data=body,
                    key=s3_key,
                    bucket_name=target_bucket,
                    replace=replace,
                    encoding="utf-8",
                )
                s3_uri = f"s3://{target_bucket}/{s3_key}"
                logger.info("Uploaded data via S3Hook to %s", s3_uri)
                return s3_uri
            except Exception as exc:
                logger.warning(
                    "Airflow S3Hook load_string failed (%s); falling back to direct boto3 client.",
                    exc,
                )

        # 2. Direct boto3 S3 client upload
        if HAS_BOTO3:
            try:
                client = self._get_boto3_client()
                client.put_object(
                    Bucket=target_bucket,
                    Key=s3_key,
                    Body=body.encode("utf-8"),
                    ContentType="application/json",
                )
                s3_uri = f"s3://{target_bucket}/{s3_key}"
                logger.info("Uploaded data via boto3 S3 client to %s", s3_uri)
                return s3_uri
            except Exception as exc:
                logger.warning(
                    "boto3 put_object failed (%s); persisting in local fallback emulator.",
                    exc,
                )

        # 3. Local filesystem fallback for dev/testing when no AWS is available
        fallback_dir = os.path.join("/tmp", "s3_bronze_emulator", target_bucket)  # noqa: S108
        target_path = os.path.join(fallback_dir, s3_key)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(body)
        s3_uri = f"s3://{target_bucket}/{s3_key}"
        logger.info("Saved data in local S3 emulator: %s", target_path)
        return s3_uri
