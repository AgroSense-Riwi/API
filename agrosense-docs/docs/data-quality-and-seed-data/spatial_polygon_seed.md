# Spatial Generation of Farms and Reserves

The generator creates deterministic synthetic polygons in WGS 84 (EPSG:4326), constrained to the configured bounding box for Colombia's coffee-growing region: latitude 1.0–7.5 and longitude -77.5–-74.5. These polygons do not represent real property boundaries or official reserve delimitations.

The code is divided by responsibility:

- `spatial.py` generates farms and reserves.
- `spatial_geometry.py` validates, serializes, and compares polygons.
- `spatial_models.py` contains the spatial result model and coordinate bounds.
- `records.py` combines geometries with the seed records.
- `run_seed.py` preserves the CLI interface.

Each polygon is provided in two formats:

- `geom_ewkt`: `SRID=4326;POLYGON(...)` text that PostGIS can read with `ST_GeomFromEWKT`.
- `polygon_geojson`: a GeoJSON Polygon object for the JSONB columns described in `docs/data_structure.md`.

The generator uses UUIDv5 for stable identifiers, validates parameters and the bounding box, closes polygon rings, and rejects rings with zero area or self-intersections. Farms include an intersection indicator computed against the generated reserves, following the contact-or-overlap semantics of `ST_Intersects`.

The executor function `generate_spatial_seed_records(farms, seed)` enriches farm records and returns the reserve records. A subsequent load step can convert `geom_ewkt` into a PostGIS `geometry(Polygon, 4326)` column. The current command generates the records and reports the count; it does not open a database connection or insert data into PostgreSQL. The `polygon_geojson` values are ready for the documented JSONB columns.

```sql
INSERT INTO farms (farm_id, farm_code, polygon_geojson, geom)
VALUES (
    :farm_id,
    :farm_code,
    CAST(:polygon_geojson AS jsonb),
    ST_GeomFromEWKT(:geom_ewkt)
);
```

The `geom` column in this example must already exist in the target schema and have the type `geometry(Polygon, 4326)`. This repository still describes `polygon_geojson` as the operational representation; enabling and populating a PostGIS column in the database requires a dedicated persistence-schema migration.
