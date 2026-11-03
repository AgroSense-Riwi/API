# syntax=docker/dockerfile:1
# ==============================================================================
# AgroSense Café — Imagen de Apache Airflow 3.3.2 con PySpark (ECS Fargate).
#
# Contexto de build: el directorio pipelines/ (el .dockerignore de la raíz lo excluye).
#   docker build -f infra/docker/airflow/Dockerfile -t agrosense-airflow pipelines
#
# Contenido:
#   /opt/airflow/dags        <- pipelines/airflow/dags
#   /opt/airflow/plugins     <- pipelines/airflow/plugins
#   /opt/airflow/spark_jobs  <- pipelines/spark_jobs (job PySpark de la capa gold)
# ==============================================================================
ARG AIRFLOW_VERSION=3.3.2
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12

ARG AIRFLOW_VERSION=3.3.2
ARG PYSPARK_VERSION=4.2.1

USER root
# Java para PySpark (default-jre-headless: OpenJDK de la versión de Debian de la imagen).
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/default-java

USER airflow
# Se fija apache-airflow para que pip no lo actualice al instalar las dependencias.
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    "pyspark==${PYSPARK_VERSION}" \
    "psycopg[binary]>=3.2" \
    "boto3>=1.35"

COPY --chown=airflow:root airflow/dags /opt/airflow/dags
COPY --chown=airflow:root airflow/plugins /opt/airflow/plugins
COPY --chown=airflow:root spark_jobs /opt/airflow/spark_jobs

ENV PYTHONPATH=/opt/airflow/spark_jobs \
    SPARK_MASTER=local[2] \
    PYSPARK_PYTHON=python
