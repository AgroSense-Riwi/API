"""AgroSense Café — Deterministic synthetic dataset generator using Faker.

Generates realistic, localized synthetic records for all 13 operational entities
with fixed PRNG seeding and deterministic UUIDv5 primary keys derived from
natural business keys (Deliverable E-03, CT-06).
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from faker import Faker

from scripts.seed.spatial import PostGISSpatialGenerator
from scripts.seed.spatial_geometry import polygon_to_wkt
from scripts.seed.spatial_models import AGROSENSE_NAMESPACE

DEFAULT_MUNICIPALITIES: list[str] = [
    "Pitalito - Huila",
    "Garzón - Huila",
    "Acevedo - Huila",
    "San Agustín - Huila",
    "La Plata - Huila",
    "Andes - Antioquia",
    "Jericó - Antioquia",
    "Fredonia - Antioquia",
    "Ciudad Bolívar - Antioquia",
    "Santa Bárbara - Antioquia",
    "Chinchiná - Caldas",
    "Manizales - Caldas",
    "Palestina - Caldas",
    "Villamaría - Caldas",
    "Armenia - Quindío",
    "Calarcá - Quindío",
    "Circasia - Quindío",
    "Montenegro - Quindío",
    "Pereira - Risaralda",
    "Santa Rosa de Cabal - Risaralda",
    "Belén de Umbría - Risaralda",
    "Ibagué - Tolima",
    "Líbano - Tolima",
    "Planadas - Tolima",
    "Popayán - Cauca",
]

DEFAULT_COFFEE_VARIETIES: list[str] = [
    "Castillo Naranjal",
    "Castillo Rosario",
    "Caturra",
    "Colombia",
    "Geisha",
    "Bourbon Rosado",
    "Tabi",
    "Typica",
    "Cenicafé 1",
    "Maragogipe",
]

DEFAULT_RESERVES: list[str] = [
    "Parque Nacional Natural Cueva de los Guácharos",
    "Parque Nacional Natural Los Nevados",
    "Reserva Forestal Protectora Central",
    "Santuario de Fauna y Flora Otún Quimbaya",
    "Parque Nacional Natural Puracé",
    "Reserva Natural de las Aves Giles-Fuertes",
]

FARM_NAME_PREFIXES: list[str] = [
    "Finca La Esperanza",
    "Hacienda El Paraíso",
    "Finca El Mirador",
    "Villa María",
    "Granja La Primavera",
    "Finca San José",
    "Hacienda Santa Rita",
    "Finca El Porvenir",
    "Finca Buena Vista",
    "Finca La Colina",
    "Hacienda El Roble",
    "Finca Bellavista",
]


class AgroSenseFakerGenerator:
    """Generador determinista de datos sintéticos para las 13 tablas del modelo AgroSense."""

    def __init__(self, seed: int = 42) -> None:
        """Inicializa el generador con semilla reproducible y Faker localizado."""
        self.seed = seed
        self.rng = random.Random(seed)
        self.faker_es = Faker("es_CO")
        self.faker_en = Faker("en_US")
        self.faker_es.seed_instance(seed)
        self.faker_en.seed_instance(seed)
        self.spatial_gen = PostGISSpatialGenerator(seed=seed)

    @staticmethod
    def get_uuid5(natural_key: str) -> uuid.UUID:
        """Genera un UUIDv5 determinista en el namespace de AgroSense."""
        return uuid.uuid5(AGROSENSE_NAMESPACE, natural_key)

    def generate_municipalities(self) -> list[dict[str, Any]]:
        """Genera catálogo de municipios cafeteros."""
        return [{"name": name} for name in DEFAULT_MUNICIPALITIES]

    def generate_coffee_varieties(self) -> list[dict[str, Any]]:
        """Genera catálogo de variedades de café."""
        return [{"variety_name": name} for name in DEFAULT_COFFEE_VARIETIES]

    def generate_users(self, count: int = 10) -> list[dict[str, Any]]:
        """Genera usuarios institucionales (Jefes de Catación, Agrónomos, Admins)."""
        roles = ["QUALITY_CHIEF", "AGRONOMIST", "ADMIN", "AUDITOR"]
        users: list[dict[str, Any]] = []
        for i in range(1, count + 1):
            role = "QUALITY_CHIEF" if i <= 3 else roles[i % len(roles)]
            name = self.faker_es.name()
            user_id = self.get_uuid5(f"USER-{i:04d}-{role}")
            users.append(
                {
                    "user_id": user_id,
                    "full_name": name,
                    "role": role,
                }
            )
        return users

    def generate_producers(self, count: int = 30) -> list[dict[str, Any]]:
        """Genera caficultores colombianos con cédulas únicas."""
        producers: list[dict[str, Any]] = []
        for i in range(1, count + 1):
            doc_number = f"CC-10{i:06d}"
            producer_id = self.get_uuid5(doc_number)
            full_name = self.faker_es.name()
            phone = f"+57310{self.rng.randint(1000000, 9999999)}"
            email = f"productor_{i:04d}@cafetero.co"
            days_ago = self.rng.randint(365, 365 * 10)
            join_date = date(2026, 1, 1) - timedelta(days=days_ago)
            producers.append(
                {
                    "producer_id": producer_id,
                    "document_number": doc_number,
                    "full_name": full_name,
                    "phone_number": phone,
                    "email": email,
                    "cooperative_join_date": join_date,
                    "is_active": True,
                }
            )
        return producers

    def generate_international_buyers(self, count: int = 10) -> list[dict[str, Any]]:
        """Genera compradores internacionales registrados."""
        buyers: list[dict[str, Any]] = []
        companies = [
            ("Nordic Specialty Roasters AS", "BUYER-EU-001"),
            ("Pacific Northwest Coffee Imports LLC", "BUYER-US-002"),
            ("Tokyo Artisanal Roasters Co", "BUYER-JP-003"),
            ("Amsterdam Green Coffee Trading BV", "BUYER-NL-004"),
            ("Milano Espresso Selection SpA", "BUYER-IT-005"),
            ("Berlin Craft Coffee GmbH", "BUYER-DE-006"),
            ("Toronto Specialty Beans Inc", "BUYER-CA-007"),
            ("Seoul Premium Coffee Ltd", "BUYER-KR-008"),
            ("Melbourne Single Origin Traders", "BUYER-AU-009"),
            ("Zurich Alpine Coffee Importers AG", "BUYER-CH-010"),
        ]
        for idx in range(min(count, len(companies))):
            c_name, client_id = companies[idx]
            buyers.append(
                {
                    "buyer_id": self.get_uuid5(client_id),
                    "client_id": client_id,
                    "buyer_name": c_name,
                }
            )
        return buyers

    def generate_farms(
        self,
        count: int = 50,
        producers: list[dict[str, Any]] | None = None,
        municipalities: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Genera fincas cafeteras georreferenciadas con indicadores ambientales."""
        prods = producers or self.generate_producers(count=max(count // 2, 5))
        munis = municipalities or self.generate_municipalities()
        farms: list[dict[str, Any]] = []

        # Coordenadas base en el eje cafetero y Huila (lat 1.8 a 5.5, lon -76.5 a -75.0)
        base_coords = [
            (1.853610, -76.050830),  # Pitalito
            (2.216670, -75.633330),  # Garzón
            (5.068890, -75.517380),  # Manizales
            (4.982500, -75.604720),  # Chinchiná
            (4.533890, -75.681110),  # Armenia
            (4.813330, -75.696110),  # Pereira
            (5.656110, -75.882220),  # Andes
        ]

        for i in range(1, count + 1):
            farm_code = f"FIN-COL-{i:04d}"
            farm_id = self.get_uuid5(farm_code)
            producer = prods[(i - 1) % len(prods)]
            muni_name = munis[(i - 1) % len(munis)]["name"]

            center_lat, center_lon = base_coords[(i - 1) % len(base_coords)]
            lat_jitter = (self.rng.random() - 0.5) * 0.08
            lon_jitter = (self.rng.random() - 0.5) * 0.08
            lat = round(center_lat + lat_jitter, 6)
            lon = round(center_lon + lon_jitter, 6)

            cultivated_area = round(self.rng.uniform(3.5, 35.0), 2)
            water_usage = round(self.rng.uniform(3.2, 5.8), 2)
            overlaps = (i % 5) == 0  # 20% solapan reserva forestal

            farms.append(
                {
                    "farm_id": farm_id,
                    "farm_code": farm_code,
                    "producer_id": producer["producer_id"],
                    "municipality_name": muni_name,
                    "latitude": Decimal(str(lat)),
                    "longitude": Decimal(str(lon)),
                    "cultivated_area_ha": Decimal(str(cultivated_area)),
                    "water_usage_m3_ton": Decimal(str(water_usage)),
                    "overlaps_reserve": overlaps,
                }
            )
        return farms

    def generate_farm_reserve_overlaps(self, farms: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Genera geometrías PostGIS de solapamiento con reservas forestales (RN-09)."""
        overlaps: list[dict[str, Any]] = []
        for farm in farms:
            if not farm.get("overlaps_reserve"):
                continue
            farm_id = farm["farm_id"]
            farm_code = farm["farm_code"]
            lat = float(farm["latitude"])
            lon = float(farm["longitude"])

            reserve_name = DEFAULT_RESERVES[abs(hash(farm_code)) % len(DEFAULT_RESERVES)]
            ring = self.spatial_gen.generate_irregular_polygon(
                center_lat=lat, center_lon=lon, area_ha=2.5, sides=5
            )
            wkt = polygon_to_wkt(ring)
            overlap_id = self.get_uuid5(f"{farm_code}:reserve_overlap")

            overlaps.append(
                {
                    "overlap_id": overlap_id,
                    "farm_id": farm_id,
                    "reserve_name": reserve_name,
                    "overlap_geometry": wkt,
                }
            )
        return overlaps

    def generate_farm_plots(
        self, farms: list[dict[str, Any]], varieties: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Genera entre 1 y 3 parcelas productivas por finca con variedades específicas."""
        plots: list[dict[str, Any]] = []
        plot_names = [
            "Lote El Mirador",
            "Lote La Esperanza",
            "Lote El Cedro",
            "Lote La Loma",
            "Lote Bella Vista",
        ]

        for farm in farms:
            farm_id = farm["farm_id"]
            farm_code = farm["farm_code"]
            num_plots = (abs(hash(farm_code)) % 3) + 1

            for p_idx in range(num_plots):
                plot_name = f"{plot_names[p_idx % len(plot_names)]} {p_idx + 1}"
                plot_id = self.get_uuid5(f"{farm_code}:{plot_name}")
                variety = varieties[(p_idx + abs(hash(farm_code))) % len(varieties)]
                crop_age = (abs(hash(farm_code)) % 8) + 2

                plots.append(
                    {
                        "plot_id": plot_id,
                        "farm_id": farm_id,
                        "variety_name": variety["variety_name"],
                        "plot_name": plot_name,
                        "crop_age_years": crop_age,
                    }
                )
        return plots

    def generate_harvest_deliveries(self, plots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Genera entregas de café recolectado por parcela."""
        deliveries: list[dict[str, Any]] = []
        start_date = date(2026, 8, 1)

        for i, plot in enumerate(plots, start=1):
            plot_id = plot["plot_id"]
            farm_id = plot["farm_id"]
            delivery_date = start_date + timedelta(days=(i * 3) % 60)
            weight = round(self.rng.uniform(800.0, 3200.0), 2)
            delivery_id = self.get_uuid5(f"DELIVERY-{plot_id}-{delivery_date}")

            deliveries.append(
                {
                    "delivery_id": delivery_id,
                    "farm_id": farm_id,
                    "plot_id": plot_id,
                    "delivery_date": delivery_date,
                    "delivered_weight_kg": Decimal(str(weight)),
                }
            )
        return deliveries

    def generate_export_batches(
        self,
        deliveries: list[dict[str, Any]],
        count: int = 15,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Genera lotes de exportación y su composición de fincas trazable (RN-08)."""
        batches: list[dict[str, Any]] = []
        compositions: list[dict[str, Any]] = []

        if not deliveries:
            return batches, compositions

        deliveries_per_batch = max(1, len(deliveries) // count)

        for b_idx in range(1, count + 1):
            batch_code = f"EXP-COL-2026-{b_idx:03d}"
            batch_id = self.get_uuid5(batch_code)

            start = (b_idx - 1) * deliveries_per_batch
            batch_dels = deliveries[start : start + deliveries_per_batch]
            if not batch_dels:
                batch_dels = [deliveries[(b_idx - 1) % len(deliveries)]]

            total_weight = sum(d["delivered_weight_kg"] for d in batch_dels)
            is_exportable = True
            is_certified = True
            weighted_water = Decimal("4.15")

            batches.append(
                {
                    "batch_id": batch_id,
                    "batch_code": batch_code,
                    "total_weight_kg": total_weight,
                    "traced_weight_kg": total_weight,
                    "is_exportable": is_exportable,
                    "is_certified": is_certified,
                    "weighted_water_m3_ton": weighted_water,
                }
            )

            for d in batch_dels:
                contributed = d["delivered_weight_kg"]
                share = round((contributed / total_weight) * Decimal("100.00"), 2)
                comp_id = self.get_uuid5(f"{batch_code}:{d['delivery_id']}")
                compositions.append(
                    {
                        "composition_id": comp_id,
                        "batch_id": batch_id,
                        "delivery_id": d["delivery_id"],
                        "farm_id": d["farm_id"],
                        "contributed_kg": contributed,
                        "share_percentage": share,
                    }
                )

        return batches, compositions

    def generate_cupping_evaluations(
        self, batches: list[dict[str, Any]], users: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Genera cataciones SCA vinculadas a jefes de calidad."""
        quality_chiefs = [u for u in users if u["role"] == "QUALITY_CHIEF"]
        if not quality_chiefs:
            quality_chiefs = users

        evaluations: list[dict[str, Any]] = []
        sensory_options = [
            "Notas a panela dulce, acidez cítrica brillante de naranja y cuerpo sedoso.",
            "Aroma floral a jazmín, notas a frutos rojos, manzana verde y postgusto prolongado.",
            "Perfil achocolatado con caramelo, avellana tostada y acidez málica equilibrada.",
            "Frutas amarillas, durazno maduro, miel de abeja y cuerpo aterciopelado consistente.",
        ]

        for i, batch in enumerate(batches):
            batch_id = batch["batch_id"]
            chief = quality_chiefs[i % len(quality_chiefs)]
            cupping_id = self.get_uuid5(f"{batch['batch_code']}:cupping")
            sca_score = Decimal(str(round(self.rng.uniform(84.50, 89.25), 2)))
            acidity = Decimal(str(round(self.rng.uniform(8.00, 8.75), 2)))
            body = Decimal(str(round(self.rng.uniform(8.00, 8.50), 2)))
            notes = sensory_options[i % len(sensory_options)]

            evaluations.append(
                {
                    "cupping_id": cupping_id,
                    "batch_id": batch_id,
                    "quality_chief_id": chief["user_id"],
                    "sca_total_score": sca_score,
                    "acidity_score": acidity,
                    "body_score": body,
                    "sensory_notes": notes,
                }
            )
        return evaluations

    def generate_buyer_batch_contracts(
        self, batches: list[dict[str, Any]], buyers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Genera contratos comerciales entre compradores y lotes certificados (RN-11)."""
        contracts: list[dict[str, Any]] = []
        if not buyers:
            return contracts

        for i, batch in enumerate(batches):
            buyer = buyers[i % len(buyers)]
            contract_id = self.get_uuid5(f"{batch['batch_code']}:{buyer['client_id']}")
            contracts.append(
                {
                    "contract_id": contract_id,
                    "batch_id": batch["batch_id"],
                    "buyer_id": buyer["buyer_id"],
                }
            )
        return contracts

    def generate_all(self, mode: str = "dev") -> dict[str, list[dict[str, Any]]]:
        """Genera el dataset sintético completo según el perfil de volumen ('dev' o 'prod')."""
        is_prod = mode == "prod"
        farms_count = 1300 if is_prod else 50
        producers_count = 200 if is_prod else 25
        users_count = 20 if is_prod else 8
        buyers_count = 10
        batches_count = 100 if is_prod else 12

        municipalities = self.generate_municipalities()
        coffee_varieties = self.generate_coffee_varieties()
        users = self.generate_users(count=users_count)
        producers = self.generate_producers(count=producers_count)
        buyers = self.generate_international_buyers(count=buyers_count)

        farms = self.generate_farms(
            count=farms_count,
            producers=producers,
            municipalities=municipalities,
        )
        overlaps = self.generate_farm_reserve_overlaps(farms)
        plots = self.generate_farm_plots(farms, coffee_varieties)
        deliveries = self.generate_harvest_deliveries(plots)
        batches, compositions = self.generate_export_batches(deliveries, count=batches_count)
        cuppings = self.generate_cupping_evaluations(batches, users)
        contracts = self.generate_buyer_batch_contracts(batches, buyers)

        return {
            "municipalities": municipalities,
            "coffee_varieties": coffee_varieties,
            "users": users,
            "producers": producers,
            "international_buyers": buyers,
            "farms": farms,
            "farm_reserve_overlaps": overlaps,
            "farm_plots": plots,
            "harvest_deliveries": deliveries,
            "export_batches": batches,
            "batch_farm_compositions": compositions,
            "cupping_evaluations": cuppings,
            "buyer_batch_contracts": contracts,
        }
