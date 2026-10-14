"""Automated Idempotency Verification Test Suite (CT-06).

Verifies strict idempotency, zero row inflation, and cryptographic checksum convergence
across dual executions of the AgroSense Café seed engine per Deliverable E-03 and
acceptance criterion CT-06.
"""

from __future__ import annotations

import pytest

from scripts.seed.faker_generators import AgroSenseFakerGenerator
from scripts.seed.run_seed import (
    SeedConfig,
    calculate_database_checksum,
    get_database_row_counts,
    get_database_state,
    reset_database_state,
    run_seed,
)
from scripts.seed.upsert_engine import (
    TABLE_UPSERT_REGISTRY,
    TOPOLOGICAL_TABLE_ORDER,
    UpsertEngine,
)


@pytest.fixture(autouse=True)
def clean_database_state():
    """Ensure database state is clean before and after each test."""
    reset_database_state()
    yield
    reset_database_state()


def test_seed_idempotency_ct06():
    """Automated Idempotency Assertion Test (pytest / CT-06).

    Verifies that running the seed engine twice with the exact same parameters
    (seed_value=42, mode='dev') yields identical state checksums without raising
    key conflicts or causing data divergence (seed-dataset-validation-criteria-en.md).
    """
    # 1. Initial execution
    exit_code_1 = run_seed(seed_value=42, mode="dev")
    assert exit_code_1 == 0, "Initial seed execution failed"
    hash_run_1 = calculate_database_checksum()
    counts_run_1 = get_database_row_counts()

    # 2. Re-execution (UPSERT in-place update / DO NOTHING)
    exit_code_2 = run_seed(seed_value=42, mode="dev")
    assert exit_code_2 == 0, "Re-execution of seed failed"
    hash_run_2 = calculate_database_checksum()
    counts_run_2 = get_database_row_counts()

    # 3. Assert zero row inflation across all tables
    for table_name, count_1 in counts_run_1.items():
        count_2 = counts_run_2.get(table_name, 0)
        assert count_1 == count_2, (
            f"Row inflation detected in table '{table_name}': "
            f"Run 1 had {count_1} rows, Run 2 has {count_2} rows (CT-06 violation)"
        )

    # 4. Assert cryptographic checksum convergence (100% deterministic state)
    assert (
        hash_run_1 == hash_run_2
    ), "Assertion Error: Seed execution is not idempotent (CT-06 violation)"


def test_zero_row_inflation_across_all_tables_dual_execution():
    """Verify that every single operational table has 0 row inflation on pass 2."""
    run_seed(seed_value=42, mode="dev")
    initial_counts = get_database_row_counts()

    # Verify all expected tables were populated
    assert len(initial_counts) >= 15, "Not all expected tables were populated in pass 1"

    # Second pass
    run_seed(seed_value=42, mode="dev")
    final_counts = get_database_row_counts()

    for table in TOPOLOGICAL_TABLE_ORDER:
        if table in initial_counts:
            row_inflation = final_counts[table] - initial_counts[table]
            assert (
                row_inflation == 0
            ), f"Table '{table}' experienced row inflation of {row_inflation} rows"


def test_dual_execution_with_seed_config_object():
    """Verify idempotency when passing explicit SeedConfig objects."""
    config = SeedConfig(mode="dev", seed=42, defect_rate=0.05, reset=False, batch_size=500)

    # Pass 1
    assert run_seed(config) == 0
    checksum_1 = calculate_database_checksum()
    state_1_counts = get_database_row_counts()

    # Pass 2
    assert run_seed(config) == 0
    checksum_2 = calculate_database_checksum()
    state_2_counts = get_database_row_counts()

    assert checksum_1 == checksum_2
    assert state_1_counts == state_2_counts


def test_triple_execution_idempotency_stability():
    """Verify that executing 3 consecutive times remains perfectly stable."""
    run_seed(seed_value=42, mode="dev")
    hash_1 = calculate_database_checksum()
    counts_1 = get_database_row_counts()

    run_seed(seed_value=42, mode="dev")
    hash_2 = calculate_database_checksum()

    run_seed(seed_value=42, mode="dev")
    hash_3 = calculate_database_checksum()
    counts_3 = get_database_row_counts()

    assert hash_1 == hash_2 == hash_3
    assert counts_1 == counts_3


def test_reset_flag_reproduces_identical_state():
    """Verify that running with reset=True clears and re-populates to the same hash."""
    # Run 1: initial run
    run_seed(seed_value=42, mode="dev")
    hash_standard = calculate_database_checksum()

    # Run 2: reset run (TRUNCATE and re-seed)
    run_seed(seed_value=42, mode="dev", reset=True)
    hash_after_reset = calculate_database_checksum()

    assert (
        hash_standard == hash_after_reset
    ), "Reset execution did not produce the identical deterministic state"


def test_different_seeds_produce_divergent_checksums():
    """Verify that different seeds produce distinct database hashes (non-trivial test)."""
    run_seed(seed_value=42, mode="dev", reset=True)
    hash_seed_42 = calculate_database_checksum()

    run_seed(seed_value=99, mode="dev", reset=True)
    hash_seed_99 = calculate_database_checksum()

    assert (
        hash_seed_42 != hash_seed_99
    ), "Different seeds must generate distinct datasets and checksums"


def test_engine_verify_idempotency_deep_inspection():
    """Inspect low-level UpsertEngine verify_idempotency report metrics for all tables."""
    faker_gen = AgroSenseFakerGenerator(seed=42)
    dataset = faker_gen.generate_all(mode="dev")

    engine = UpsertEngine(batch_size=250)
    report = engine.verify_idempotency(dataset)

    assert report["is_idempotent"] is True
    assert report["row_inflation_detected"] is False
    assert report["tables_tested"] == len(set(dataset) & TABLE_UPSERT_REGISTRY.keys())

    for table_name, result in report["table_results"].items():
        assert result["passed"] is True
        assert result["row_inflation"] == 0
        assert result["initial_count"] == result["re_run_count"]
        assert result["checksum_match"] is True
        assert result["pass1_stats"]["inserted"] == result["initial_count"]
        assert (
            result["pass2_stats"]["inserted"] == 0
        ), f"Table {table_name} inserted new rows on pass 2 (inflation violation)"


def test_row_inflation_detection_on_artificial_mutation(monkeypatch):
    """Verify that test suite flags an error if row inflation is artificially introduced."""
    # First normal run
    run_seed(seed_value=42, mode="dev")
    hash_run_1 = calculate_database_checksum()

    # Artificially inject an extra row into the global state before pass 2
    global_state = get_database_state()
    farms_store = global_state.get("farms", {})
    artificial_key = ("FIN-9999",)
    farms_store[artificial_key] = {
        "farm_id": "00000000-0000-0000-0000-000000000000",
        "farm_code": "FIN-9999",
        "name": "Artificial Farm",
    }

    hash_run_mutated = calculate_database_checksum()
    assert hash_run_1 != hash_run_mutated, "Checksum failed to detect extra injected row"


def test_dev_mode_expected_row_counts_alignment():
    """Verify that dev mode generates record counts aligned with data_structure.md."""
    run_seed(seed_value=42, mode="dev")
    counts = get_database_row_counts()

    # Master catalog sizes in dev
    assert counts["departments"] == 10
    assert counts["municipalities"] == 23
    assert counts["coffee_varieties"] == 8
    assert counts["sensor_types"] == 4
    assert counts["crop_nutrient_references"] == 32
    assert counts["forest_reserves"] == 3

    # Core entities in dev
    assert counts["farms"] == 50
    assert counts["producers"] == 25
    assert counts["international_buyers"] == 10
    assert counts["iot_sensors"] == 45  # 15 pilot farms x 3 sensors
    assert counts["harvest_deliveries"] == 80
    assert counts["export_batches"] == 10
    assert counts["buyer_batch_contracts"] == 10
