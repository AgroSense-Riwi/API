"""AgroSense Café — IoT Sensor Simulator Entrypoint (AGRO-15 / EP-05).

Orchestrates deterministic 900-sensor simulation across 300 pilot farms,
nominal streaming every 10 minutes, reconnection burst transmissions (RNF-01),
and hardware defect injections (DEF-01, DEF-02, DEF-03, Low Battery).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

from agrosense_simulators.amqp_publisher import RabbitMQPublisher
from agrosense_simulators.anomaly_injector import AnomalyInjector
from agrosense_simulators.config import SimulatorConfig
from agrosense_simulators.sensor_generator import SensorNetwork

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("agrosense.simulators.main")


def build_parser() -> argparse.ArgumentParser:
    """Configura los argumentos de línea de comandos para el simulador."""
    parser = argparse.ArgumentParser(
        prog="agrosense_simulators",
        description=(
            "AgroSense Café — IoT Telemetry Simulator with Block/Burst Transmission (AGRO-15)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["dev", "prod", "burst", "dry-run"],
        default="dev",
        help=(
            "Modo de ejecución del simulador (dev=ciclo corto/burst, "
            "prod=bucle continuo, burst=solo ráfagas, dry-run=sin RabbitMQ)."
        ),
    )
    parser.add_argument(
        "--farms",
        type=int,
        default=300,
        help="Cantidad de fincas piloto a simular (por defecto 300 -> 900 sensores).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla determinista PRNG (por defecto 42).",
    )
    parser.add_argument(
        "--days-disconnect",
        type=int,
        default=None,
        dest="days_disconnect",
        help="Sobrescribe los días de desconexión forzados para ráfagas (1 a 7 días, RNF-01).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Número de iteraciones nominales a simular en modo dev.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=0.0,
        dest="interval_seconds",
        help=(
            "Segundos de espera en tiempo real entre ciclos nominales "
            "(0.0 para ejecución inmediata en dev/batch)."
        ),
    )
    parser.add_argument(
        "--rabbitmq-url",
        type=str,
        default=None,
        dest="rabbitmq_url",
        help="URL AMQP del broker RabbitMQ (sobrescribe RABBITMQ_URL de .env).",
    )
    return parser


def run_simulation(args: argparse.Namespace) -> int:
    """Ejecuta la orquestación del simulador de acuerdo con la configuración CLI."""
    logger.info("==============================================================================")
    logger.info("🚀 INICIANDO SIMULADOR DE TELEMETRÍA IOT AGROSENSE CAFÉ (AGRO-15)")
    logger.info("==============================================================================")

    # 1. Cargar configuración base
    config = SimulatorConfig()
    if args.farms:
        config.farms_count = args.farms
    if args.seed:
        config.seed_value = args.seed
    if args.rabbitmq_url:
        config.rabbitmq_url = args.rabbitmq_url
    if args.mode:
        config.mode = args.mode

    mock_mode = args.mode == "dry-run"

    logger.info(
        "Parámetros: mode=%s, fincas=%d (sensores=%d), seed=%d, interval_minutes=%d",
        config.mode,
        config.farms_count,
        config.farms_count * config.sensors_per_farm,
        config.seed_value,
        config.reading_interval_minutes,
    )

    # 2. Inicializar red determinista de sensores
    network = SensorNetwork(
        farms_count=config.farms_count,
        firmware_version=config.firmware_version,
        seed=config.seed_value,
    )
    logger.info(
        "✅ Red de sensores inicializada: %d sensores creados deterministamente con UUIDv5.",
        network.total_sensors,
    )

    # 3. Inyectar anomalías configurables
    injector = AnomalyInjector(config)
    injector.apply_defects(network.sensors)

    if args.days_disconnect is not None:
        for sensor in network.sensors:
            if sensor.is_disconnected:
                sensor.disconnect_days = args.days_disconnect

    # 4. Inicializar cliente RabbitMQ
    publisher = RabbitMQPublisher(
        rabbitmq_url=config.rabbitmq_url,
        exchange_name=config.exchange_name,
        mock_mode=mock_mode,
    )

    if not mock_mode:
        connected = publisher.connect()
        if not connected:
            logger.warning(
                "⚠️ No se pudo conectar a RabbitMQ en %s. "
                "Continuando en modo degradado/mock para validar generación de datos.",
                config.rabbitmq_url,
            )
            publisher.mock_mode = True

    current_sim_dt = datetime.now(UTC)
    total_messages_emitted = 0

    try:
        # 5. Escenario: Transmisión en Ráfaga (Burst por Reconexión Celular - DEF-03 / RNF-01)
        if config.mode in {"dev", "burst", "dry-run"}:
            logger.info("--- [FASE 1] SIMULACIÓN DE RÁFAGAS POR RECONEXIÓN CELULAR (RNF-01) ---")
            disconnected_sensors = [s for s in network.sensors if s.is_disconnected]
            logger.info(
                "Sensores desconectados acumulando búfer histórico: %d sensores",
                len(disconnected_sensors),
            )

            for sensor in disconnected_sensors:
                days = sensor.disconnect_days or 3
                intervals = days * 24 * (60 // config.reading_interval_minutes)
                burst_start_dt = current_sim_dt - timedelta(days=days)

                temp_batch_id = f"LOCAL-BUF-{sensor.sensor_id[:8]}"
                # Llenar búfer cronológico local
                for i in range(intervals):
                    step_dt = burst_start_dt + timedelta(
                        minutes=i * config.reading_interval_minutes
                    )
                    reading = sensor.step_reading(step_dt, batch_id=temp_batch_id)
                    sensor.buffer_reading(reading)

                # Reconexión celular: Emitir ráfaga compartiendo un único ingestion_batch_id
                burst_batch_id = (
                    f"BATCH-BURST-{sensor.sensor_id[:8]}-{current_sim_dt.strftime('%Y%m%d%H%M%S')}"
                )
                burst_records = sensor.flush_burst_buffer(burst_batch_id=burst_batch_id)

                # Validar orden cronológico estricto
                is_ordered = all(
                    burst_records[idx].reading_timestamp <= burst_records[idx + 1].reading_timestamp
                    for idx in range(len(burst_records) - 1)
                )
                if not is_ordered:
                    logger.error(
                        "❌ ERROR CRÍTICO: El búfer del sensor %s violó el orden cronológico.",
                        sensor.sensor_id,
                    )
                    return 1

                sent = publisher.publish_batch(burst_records)
                total_messages_emitted += sent
                logger.debug(
                    "Sensor %s (%s, finca %s): Ráfaga de %d lecturas (%d días) "
                    "emitida con batch_id=%s",
                    sensor.sensor_id,
                    sensor.sensor_type.value,
                    sensor.farm_code,
                    len(burst_records),
                    days,
                    burst_batch_id,
                )

            logger.info(
                "✅ Ráfagas de reconexión completadas: %d lecturas emitidas en bloques ordenados.",
                total_messages_emitted,
            )

        # 6. Escenario: Streaming Nominal Continuo (cada 10 minutos)
        if config.mode in {"dev", "prod", "dry-run"}:
            logger.info("--- [FASE 2] TRANSMISIÓN CONTINUA NOMINAL (STREAMING CADA 10 MIN) ---")
            online_sensors = [s for s in network.sensors if not s.is_disconnected]
            iterations = args.iterations if config.mode in {"dev", "dry-run"} else sys.maxsize

            iter_count = 0
            while iter_count < iterations:
                iter_count += 1
                stream_batch_id = f"BATCH-STREAM-{current_sim_dt.strftime('%Y%m%d-%H%M')}"
                batch_payloads = []

                for sensor in online_sensors:
                    reading = sensor.step_reading(current_sim_dt, batch_id=stream_batch_id)
                    batch_payloads.append(reading)

                sent = publisher.publish_batch(batch_payloads)
                total_messages_emitted += sent
                logger.info(
                    "Ciclo nominal #%d (%s): %d telemetrías emitidas a RabbitMQ (batch_id=%s)",
                    iter_count,
                    current_sim_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    sent,
                    stream_batch_id,
                )

                if config.mode == "prod":
                    sleep_time = (
                        args.interval_seconds
                        if args.interval_seconds > 0
                        else (config.reading_interval_minutes * 60)
                    )
                    logger.info(
                        "Esperando %s segundos hasta el siguiente ciclo nominal...",
                        sleep_time,
                    )
                    time.sleep(sleep_time)
                    current_sim_dt += timedelta(minutes=config.reading_interval_minutes)

        logger.info(
            "=============================================================================="
        )
        logger.info(
            "🎉 SIMULACIÓN COMPLETADA EXITOSAMENTE: %d mensajes totales generados/publicados.",
            total_messages_emitted,
        )
        logger.info(
            "=============================================================================="
        )
        return 0

    except KeyboardInterrupt:
        logger.info("Simulación interrumpida por el usuario (SIGINT).")
        return 0
    finally:
        publisher.close()


def main() -> None:
    """Entrypoint de ejecución directa para módulo CLI."""
    parser = build_parser()
    args = parser.parse_args()
    code = run_simulation(args)
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
