"""Feed contracts for Layer-1 ingestion.

Each Layer-0 feed has a contract that declares its required fields, the privacy
class of the feed, the role of each field, and any FORBIDDEN fields (design
invariants — e.g. ATTO must never carry an alighting field; telecom must never
carry raw coordinates or a raw subscriber number). The governance layer enforces
these; the indexing layer uses the coordinate / stop / H3 declarations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PrivacyClass(str, Enum):
    NON_PERSONAL = "non_personal"                 # vehicle telemetry, aggregates
    PERSONAL_PSEUDONYMIZED = "personal_pseudonymized"
    PERSONAL_PSEUDONYMIZED_LOCALIZED = "personal_pseudonymized_localized"  # + in-country storage


class FieldRole(str, Enum):
    IDENTIFIER = "identifier"            # pseudonymous key (must be a hash, never raw)
    QUASI_IDENTIFIER = "quasi"          # combines to re-identify (stop, time, card_type, cell)
    ASSET_ID = "asset_id"               # vehicle/route id — not personal
    COORDINATE = "coordinate"
    TIMESTAMP = "timestamp"
    MEASURE = "measure"                 # speed, occupancy (aggregate), crowding


@dataclass
class FeedContract:
    name: str
    privacy_class: PrivacyClass
    roles: dict[str, FieldRole]          # field -> role
    forbidden: list[str] = field(default_factory=list)
    coordinate_fields: tuple[str, str] | None = None   # (lat, lon) if present
    stop_id_field: str | None = None     # maps to a stop's H3 cell
    h3_field: str | None = None          # feed already carries a serving H3 cell
    timestamp_field: str = "timestamp"

    @property
    def required(self) -> list[str]:
        return list(self.roles.keys())

    @property
    def id_fields(self) -> list[str]:
        return [f for f, r in self.roles.items() if r is FieldRole.IDENTIFIER]

    @property
    def is_personal(self) -> bool:
        return self.privacy_class is not PrivacyClass.NON_PERSONAL

    @property
    def localization_required(self) -> bool:
        return self.privacy_class is PrivacyClass.PERSONAL_PSEUDONYMIZED_LOCALIZED


# --------------------------------------------------------------------------- #
# Registered contracts (all enforced now; data may not exist yet)
# --------------------------------------------------------------------------- #
R = FieldRole

GPS = FeedContract(
    name="gps",
    privacy_class=PrivacyClass.NON_PERSONAL,
    roles={
        "vehicle_id": R.ASSET_ID, "trip_id": R.ASSET_ID, "route_id": R.ASSET_ID,
        "timestamp": R.TIMESTAMP, "lat": R.COORDINATE, "lon": R.COORDINATE,
        "speed_kmh": R.MEASURE, "occupancy": R.MEASURE, "crowding_index": R.MEASURE,
        "direction": R.ASSET_ID, "vehicle_type": R.ASSET_ID,
    },
    forbidden=["passenger_id", "msisdn", "name"],   # telemetry must stay non-personal
    coordinate_fields=("lat", "lon"),
)

ATTO = FeedContract(
    name="atto",
    privacy_class=PrivacyClass.PERSONAL_PSEUDONYMIZED,
    roles={
        "hashed_card_id": R.IDENTIFIER, "timestamp": R.TIMESTAMP,
        "boarding_stop_id": R.QUASI_IDENTIFIER, "route_id": R.ASSET_ID,
        "card_type": R.QUASI_IDENTIFIER,
    },
    # Alighting blindness is a DESIGN INVARIANT, not a data gap: any tap-out field
    # must be rejected so no downstream layer silently assumes destinations exist.
    forbidden=["tap_out", "alight_stop_id", "alight_time", "pan", "card_number"],
    stop_id_field="boarding_stop_id",
)

TELECOM = FeedContract(
    name="telecom",
    privacy_class=PrivacyClass.PERSONAL_PSEUDONYMIZED_LOCALIZED,
    roles={
        "hashed_subscriber_id": R.IDENTIFIER, "event_type": R.QUASI_IDENTIFIER,
        "timestamp": R.TIMESTAMP, "h3_cell": R.QUASI_IDENTIFIER,
    },
    # NOTE: the in-country tag `processing_region` [Art. 27-1] is enforced by
    # governance (a legal-compliance check), NOT as a required schema field — so
    # its absence surfaces as a governance violation attributed to the law, not a
    # generic "missing column" schema error.
    # Coarse serving cell only — no precise coordinates; no raw network identifiers.
    forbidden=["lat", "lon", "msisdn", "imsi", "imei", "phone"],
    h3_field="h3_cell",
)

CONTRACTS: dict[str, FeedContract] = {c.name: c for c in (GPS, ATTO, TELECOM)}
