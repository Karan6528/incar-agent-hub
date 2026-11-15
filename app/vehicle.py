"""Fake car + map data so I can test without a real vehicle backend.

All in memory. Distances are rough road distances from Munich.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field

# roughly a big EV
BATTERY_CAPACITY_KWH = 105.0
FULL_RANGE_KM = 600.0
MIN_CABIN_TEMP_C = 16.0
MAX_CABIN_TEMP_C = 28.0

# map data (mocked)
# road distance from Munich in km
DESTINATIONS_KM: dict[str, float] = {
    "ingolstadt": 80,
    "nuremberg": 170,
    "stuttgart": 230,
    "frankfurt": 400,
    "leipzig": 430,
    "berlin": 585,
    "hamburg": 780,
}

# chargers along the main routes, km = distance from Munich
CHARGING_STATIONS: dict[str, list[dict]] = {
    "berlin": [
        {"id": "ion-ingolstadt", "name": "IONITY Ingolstadt Nord", "km": 80, "power_kw": 350, "free_connectors": 4},
        {"id": "ewe-nuremberg", "name": "EnBW Nürnberg-Feucht", "km": 170, "power_kw": 300, "free_connectors": 2},
        {"id": "ion-bayreuth", "name": "IONITY Bayreuth", "km": 235, "power_kw": 350, "free_connectors": 6},
        {"id": "tes-hof", "name": "Supercharger Hof", "km": 285, "power_kw": 250, "free_connectors": 0},
        {"id": "ion-leipzig", "name": "IONITY Leipzig-Schkeuditz", "km": 430, "power_kw": 350, "free_connectors": 3},
        {"id": "ewe-berlin", "name": "EnBW Berlin-Schönefeld", "km": 570, "power_kw": 150, "free_connectors": 5},
    ],
    "hamburg": [
        {"id": "ion-ingolstadt", "name": "IONITY Ingolstadt Nord", "km": 80, "power_kw": 350, "free_connectors": 4},
        {"id": "ewe-nuremberg", "name": "EnBW Nürnberg-Feucht", "km": 170, "power_kw": 300, "free_connectors": 2},
        {"id": "ion-kassel", "name": "IONITY Kassel-Ost", "km": 480, "power_kw": 350, "free_connectors": 5},
        {"id": "ion-hannover", "name": "IONITY Hannover-Lahe", "km": 630, "power_kw": 350, "free_connectors": 3},
    ],
    "stuttgart": [
        {"id": "ion-augsburg", "name": "IONITY Augsburg-Ost", "km": 70, "power_kw": 350, "free_connectors": 4},
        {"id": "ewe-ulm", "name": "EnBW Ulm-West", "km": 150, "power_kw": 150, "free_connectors": 2},
    ],
    "frankfurt": [
        {"id": "ion-ingolstadt", "name": "IONITY Ingolstadt Nord", "km": 80, "power_kw": 350, "free_connectors": 4},
        {"id": "ewe-nuremberg", "name": "EnBW Nürnberg-Feucht", "km": 170, "power_kw": 300, "free_connectors": 2},
        {"id": "ion-wuerzburg", "name": "IONITY Würzburg", "km": 280, "power_kw": 350, "free_connectors": 3},
    ],
}
# shorter routes are just the first part of the Berlin route
CHARGING_STATIONS["nuremberg"] = [s for s in CHARGING_STATIONS["berlin"] if s["km"] <= 170]
CHARGING_STATIONS["ingolstadt"] = [s for s in CHARGING_STATIONS["berlin"] if s["km"] <= 80]
CHARGING_STATIONS["leipzig"] = [s for s in CHARGING_STATIONS["berlin"] if s["km"] <= 430]


class VehicleError(ValueError):
    pass


def normalise_destination(destination: str) -> str:
    key = destination.strip().lower()
    aliases = {"nürnberg": "nuremberg", "münchen": "munich", "frankfurt am main": "frankfurt"}
    key = aliases.get(key, key)
    if key not in DESTINATIONS_KM:
        known = ", ".join(sorted(d.title() for d in DESTINATIONS_KM))
        raise VehicleError(f"Unknown destination '{destination}'. Known destinations: {known}.")
    return key


def route_distance_km(destination: str) -> float:
    return DESTINATIONS_KM[normalise_destination(destination)]


def stations_on_route(destination: str, min_power_kw: int = 0, only_available: bool = True) -> list[dict]:
    key = normalise_destination(destination)
    stations = [deepcopy(s) for s in CHARGING_STATIONS.get(key, [])]
    return [
        s for s in stations
        if s["power_kw"] >= min_power_kw and (not only_available or s["free_connectors"] > 0)
    ]


@dataclass
class VehicleState:
    vin: str = "WBY-DEMO-0000001"
    model: str = "Demo EV"
    location: str = "Munich"
    battery_pct: float = 34.0
    cabin_temp_c: float = 19.0
    climate_on: bool = False
    navigation: dict | None = None
    events: list[str] = field(default_factory=list)

    @property
    def range_km(self) -> float:
        return round(FULL_RANGE_KM * self.battery_pct / 100, 1)

    def as_dict(self) -> dict:
        data = asdict(self)
        data.pop("events")
        data["range_km"] = self.range_km
        data["battery_capacity_kwh"] = BATTERY_CAPACITY_KWH
        return data


class Vehicle:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = VehicleState()

    def reset(self) -> None:
        with self._lock:
            self._state = VehicleState()

    def status(self) -> dict:
        with self._lock:
            return self._state.as_dict()

    def set_cabin_temperature(self, temperature_c: float) -> dict:
        if not MIN_CABIN_TEMP_C <= temperature_c <= MAX_CABIN_TEMP_C:
            raise VehicleError(
                f"Temperature must be between {MIN_CABIN_TEMP_C} and {MAX_CABIN_TEMP_C} °C."
            )
        with self._lock:
            self._state.cabin_temp_c = round(temperature_c, 1)
            self._state.climate_on = True
            self._state.events.append(f"climate set to {temperature_c} °C")
            return {"cabin_temp_c": self._state.cabin_temp_c, "climate_on": True}

    def start_navigation(self, destination: str, charging_stops: list[str] | None = None) -> dict:
        key = normalise_destination(destination)
        stops = charging_stops or []
        known_ids = {s["id"]: s for s in CHARGING_STATIONS.get(key, [])}
        unknown = [s for s in stops if s not in known_ids]
        if unknown:
            raise VehicleError(f"Unknown charging stop id(s) for this route: {', '.join(unknown)}")
        route = {
            "destination": key.title(),
            "distance_km": DESTINATIONS_KM[key],
            "charging_stops": [known_ids[s]["name"] for s in stops],
            "status": "active",
        }
        with self._lock:
            self._state.navigation = route
            self._state.events.append(f"navigation to {key.title()} started")
        return route


vehicle = Vehicle()
