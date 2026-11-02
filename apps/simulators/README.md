# AgroSense Café — IoT Sensor Simulator (AGRO-15 / EP-05)

Servicio desacoplado y contenedorizado del simulador de hardware IoT para AgroSense Café.

## Capacidades

1. **Topología de Sensores Determinista**: Simulación de 900 sensores distribuidos en 300 fincas piloto (3 sensores por finca: `SOIL_MOISTURE`, `TEMPERATURE`, `PLUVIOMETER`) mediante UUIDv5 reproducibles (`AGROSENSE_NAMESPACE = 6ba7b810-9dad-11d1-80b4-00c04fd430c8`).
2. **Modelado Físico Realista**:
   - Temperatura: Ciclo diurno sinusoidal de 15 °C a 28 °C (`CELSIUS`).
   - Humedad del Suelo: Dinámica continua entre 15% y 55% (`%`).
   - Pluviometría: Eventos esporádicos de precipitación entre 0 mm y 45 mm (`MM`).
3. **Modos de Transmisión (RNF-01)**:
   - Streaming nominal continuo cada 10 minutos.
   - Transmisión en ráfaga (*burst*) tras reconexión celular (1 a 7 días acumulados) con orden cronológico estricto y lote unificado (`ingestion_batch_id`).
4. **Inyección de Defectos Configurables (Sección 5.3)**:
   - `DEF-01` (Clock Drift): Desfases temporales de -2h a +12h en `reading_timestamp` para evaluar corrección en capa Silver.
   - `DEF-02` (Stuck Sensor): Sensores congelados con variación < 0.5% en 24h consecutivas para clasificación `DEFECTIVE_STUCK`.
   - `Batería Agotada`: Descarga progresiva reportando lecturas con `battery_level_pct < 15%`.
5. **Integración con RabbitMQ (AMQP 0-9-1)**:
   - Publicación hacia el Topic Exchange `agrosense.ingest.exchange`.
   - Routing Keys: `telemetry.block.soil_moisture`, `telemetry.block.temperature`, `telemetry.block.pluviometer`.
   - Mensajes persistentes (`delivery_mode = 2`) y reconexión automática resiliente con retroceso exponencial.
