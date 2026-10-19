"""Unit tests for the Faker synthetic generator module."""

from scripts.seed.faker_generators import AgroSenseFakerGenerator


def test_faker_generator_deterministic_reproducibility():
    """Verifica que dos generadores con la misma semilla generen datasets 100% idénticos."""
    gen_1 = AgroSenseFakerGenerator(seed=42)
    dataset_1 = gen_1.generate_all(mode="dev")

    gen_2 = AgroSenseFakerGenerator(seed=42)
    dataset_2 = gen_2.generate_all(mode="dev")

    assert dataset_1.keys() == dataset_2.keys()
    for table_name in dataset_1:
        assert dataset_1[table_name] == dataset_2[table_name], f"Mismatch in {table_name}"


def test_faker_generator_different_seeds_produce_divergence():
    """Verifica que semillas distintas produzcan valores sintéticos divergentes."""
    gen_a = AgroSenseFakerGenerator(seed=42)
    dataset_a = gen_a.generate_all(mode="dev")

    gen_b = AgroSenseFakerGenerator(seed=999)
    dataset_b = gen_b.generate_all(mode="dev")

    producers_a = [p["full_name"] for p in dataset_a["producers"]]
    producers_b = [p["full_name"] for p in dataset_b["producers"]]
    assert producers_a != producers_b

    farms_a = [f["latitude"] for f in dataset_a["farms"]]
    farms_b = [f["latitude"] for f in dataset_b["farms"]]
    assert farms_a != farms_b


def test_faker_generator_relational_integrity():
    """Verifica la integridad de referencias cruzadas en el dataset generado."""
    gen = AgroSenseFakerGenerator(seed=42)
    dataset = gen.generate_all(mode="dev")

    assert len(dataset["municipalities"]) > 0
    assert len(dataset["coffee_varieties"]) > 0
    assert len(dataset["users"]) > 0
    assert len(dataset["producers"]) > 0
    assert len(dataset["international_buyers"]) > 0
    assert len(dataset["farms"]) == 50
    assert len(dataset["farm_plots"]) >= 50
    assert len(dataset["harvest_deliveries"]) >= 50
    assert len(dataset["export_batches"]) == 12

    # Verificar que los producer_id de las fincas existen en productores
    producer_ids = {p["producer_id"] for p in dataset["producers"]}
    for farm in dataset["farms"]:
        assert farm["producer_id"] in producer_ids

    # Verificar que los farm_id de las parcelas existen en fincas
    farm_ids = {f["farm_id"] for f in dataset["farms"]}
    for plot in dataset["farm_plots"]:
        assert plot["farm_id"] in farm_ids

    # Verificar que las superposiciones corresponden a fincas con overlaps_reserve=True
    overlap_farm_ids = {f["farm_id"] for f in dataset["farms"] if f["overlaps_reserve"]}
    for overlap in dataset["farm_reserve_overlaps"]:
        assert overlap["farm_id"] in overlap_farm_ids
        assert overlap["overlap_geometry"].startswith("POLYGON((")
