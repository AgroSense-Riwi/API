"""Unit tests for the Faker synthetic generator module."""

import uuid

from scripts.seed.faker_generators import AgroSenseFakerGenerator
from scripts.seed.records import generate_faker_seed_dataset
from scripts.seed.spatial_models import AGROSENSE_NAMESPACE


def test_faker_generator_deterministic_reproducibility():
    """Verify that two generators with the same seed yield 100% identical datasets."""
    gen_1 = AgroSenseFakerGenerator(seed=42)
    dataset_1 = gen_1.generate_all(mode="dev")

    gen_2 = AgroSenseFakerGenerator(seed=42)
    dataset_2 = gen_2.generate_all(mode="dev")

    assert dataset_1.keys() == dataset_2.keys()
    for table_name in dataset_1:
        assert dataset_1[table_name] == dataset_2[table_name], f"Mismatch in {table_name}"
    assert len(dataset_1["soil_lab_reports"]) == 40
    assert len(dataset_1["soil_analyses"]) == 40
    assert sum(
        "DEFECT_MIXED_UNITS" in report["quality_defects"]
        for report in dataset_1["soil_lab_reports"]
    ) == 2
    assert all(
        "nitrogen_mg_kg" in analysis and "source_data" in analysis
        for analysis in dataset_1["soil_analyses"]
    )


def test_faker_generator_different_seeds_produce_divergence():
    """Verify that distinct seeds produce distinct synthetic outputs."""
    gen_a = AgroSenseFakerGenerator(seed=42)
    dataset_a = gen_a.generate_all(mode="dev")

    gen_b = AgroSenseFakerGenerator(seed=999)
    dataset_b = gen_b.generate_all(mode="dev")

    # While catalogs like departments and varieties are static, generated entities diverge
    producers_a = [p["full_name"] for p in dataset_a["producers"]]
    producers_b = [p["full_name"] for p in dataset_b["producers"]]
    assert producers_a != producers_b

    farms_a = [f["name"] for f in dataset_a["farms"]]
    farms_b = [f["name"] for f in dataset_b["farms"]]
    assert farms_a != farms_b


def test_uuid5_deterministic_derivation():
    """Verify deterministic derivation of UUIDv5 primary keys from natural keys."""
    gen = AgroSenseFakerGenerator(seed=42)

    farm_code = "FIN-0001"
    expected_farm_uuid = str(uuid.uuid5(AGROSENSE_NAMESPACE, farm_code))
    assert gen.get_uuid5(farm_code) == expected_farm_uuid
    assert gen.get_uuid5(farm_code) == "7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9"

    client_id = "BUY-INT-001"
    expected_buyer_uuid = str(uuid.uuid5(AGROSENSE_NAMESPACE, client_id))
    assert gen.get_uuid5(client_id) == expected_buyer_uuid


def test_catalogs_and_independent_entities():
    """Verify Tier 1 reference catalogs and actors generation."""
    gen = AgroSenseFakerGenerator(seed=42)

    departments = gen.generate_departments()
    assert len(departments) == 10
    assert departments[0]["dane_code"] == "05"
    assert departments[0]["name"] == "Antioquia"

    varieties = gen.generate_coffee_varieties()
    assert len(varieties) >= 8
    names = {v["name"] for v in varieties}
    assert "Castillo" in names
    assert "Geisha" in names

    sensor_types = gen.generate_sensor_types()
    assert len(sensor_types) == 4
    type_codes = {st["type_code"] for st in sensor_types}
    assert {"SOIL_MOISTURE", "TEMPERATURE", "PLUVIOMETER", "MULTI_AGRO"} == type_codes

    nutrients = gen.generate_crop_nutrient_references(varieties)
    assert len(nutrients) == len(varieties) * 4  # 4 phenological stages
    assert all(n["req_nitrogen_kg_ha"] > 0 for n in nutrients)


def test_producers_and_users_structure():
    """Verify generated Colombian producers and system users."""
    gen = AgroSenseFakerGenerator(seed=42)

    users = gen.generate_users(count=5)
    assert len(users) == 5
    for user in users:
        assert user["email"].endswith("@agrosense.co")
        assert len(user["password_hash"]) == 64
        assert user["role"] in {"ADMIN", "AGRONOMIST", "PRODUCER", "AUDITOR"}

    producers = gen.generate_producers(count=10)
    assert len(producers) == 10
    for prod in producers:
        assert prod["document_number"].startswith("CC-109")
        assert prod["phone_number"].startswith("+57 3")
        assert "@" in prod["email"]


def test_international_buyers_structure():
    """Verify international buyers generation."""
    gen = AgroSenseFakerGenerator(seed=42)
    buyers = gen.generate_international_buyers(count=8)
    assert len(buyers) == 8
    for buyer in buyers:
        assert buyer["client_id"].startswith("BUY-INT-")
        assert len(buyer["api_key_hash"]) == 64
        assert "Coffee Traders" in buyer["company_name"]


def test_farms_plots_and_spatial_attributes():
    """Verify farms generation with PostGIS geometries and plots."""
    gen = AgroSenseFakerGenerator(seed=42)
    farms = gen.generate_farms(count=6, pilot_count=3)
    assert len(farms) == 6
    pilot_count = sum(1 for f in farms if f["has_pilot_sensors"])
    assert pilot_count == 3

    for farm in farms:
        assert farm["farm_code"].startswith("FIN-")
        assert farm["geom_ewkt"].startswith("SRID=4326;POLYGON((")
        assert farm["polygon_geojson"]["type"] == "Polygon"
        assert 1.0 <= farm["latitude"] <= 7.5
        assert -77.5 <= farm["longitude"] <= -74.5

    plots = gen.generate_farm_plots(farms, plots_per_farm=2)
    assert len(plots) == 12
    for plot in plots:
        assert plot["cultivated_area_ha"] > 0
        assert plot["tree_density_per_ha"] >= 4500


def test_iot_sensors_allocation():
    """Verify exactly 3 sensors per pilot farm."""
    gen = AgroSenseFakerGenerator(seed=42)
    farms = gen.generate_farms(count=10, pilot_count=4)
    sensors = gen.generate_iot_sensors(farms)
    assert len(sensors) == 12  # 4 pilot farms * 3 sensors

    sensor_types_count: dict[str, int] = {}
    for sensor in sensors:
        stype = sensor["sensor_type"]
        sensor_types_count[stype] = sensor_types_count.get(stype, 0) + 1
        assert sensor["battery_level"] >= 80.0
        assert sensor["status"] == "ACTIVE"

    assert sensor_types_count == {"SOIL_MOISTURE": 4, "TEMPERATURE": 4, "PLUVIOMETER": 4}


def test_traceability_and_batch_entities():
    """Verify harvest deliveries, export batches, and commercial contracts."""
    gen = AgroSenseFakerGenerator(seed=42)
    farms = gen.generate_farms(count=4, pilot_count=2)
    deliveries = gen.generate_harvest_deliveries(farms, count=8)
    assert len(deliveries) == 8

    batches = gen.generate_export_batches(count=3)
    assert len(batches) == 3

    compositions = gen.generate_batch_farm_compositions(batches, deliveries)
    assert len(compositions) == 6

    buyers = gen.generate_international_buyers(count=2)
    contracts = gen.generate_buyer_batch_contracts(batches, buyers)
    assert len(contracts) == 3
    assert all(c["shipping_container_id"].startswith("MSCU-") for c in contracts)


def test_generate_faker_seed_dataset_helper():
    """Verify records.py generate_faker_seed_dataset convenience function."""
    dataset = generate_faker_seed_dataset(seed=42, mode="dev")
    assert "farms" in dataset
    assert len(dataset["farms"]) == 50
    assert len(dataset["departments"]) == 10


def test_faker_edge_cases_empty_inputs():
    """Verify empty inputs handling for overlaps, compositions, and contracts."""
    gen = AgroSenseFakerGenerator(seed=42)
    assert gen.generate_farm_reserve_overlaps([], []) == []
    assert gen.generate_batch_farm_compositions([], []) == []
    assert gen.generate_buyer_batch_contracts([], []) == []
