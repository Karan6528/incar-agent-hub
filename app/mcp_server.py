"""MCP server with the car functions as tools.

Any MCP client can connect to /mcp and use these (my host agent, Claude,
the MCP inspector...).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from app.vehicle import (
    CHARGING_STATIONS,
    DESTINATIONS_KM,
    VehicleError,
    normalise_destination,
    stations_on_route,
    vehicle,
)

mcp = FastMCP(
    name="demo-vehicle",
    instructions=(
        "Tools for a demo electric car parked in Munich. Read the vehicle status before "
        "making decisions about range or charging. Temperatures are in °C, distances in km."
    ),
    stateless_http=True,  # no sessions -> works with multiple instances on App Runner
    json_response=True,
    # DNS rebinding protection blocks the App Runner domain, the API key covers it instead
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
CHANGES_CAR = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=READ_ONLY)
def get_vehicle_status() -> dict:
    """Get the car's current state: battery %, remaining range (km), cabin temperature, location and active navigation."""
    return vehicle.status()


@mcp.tool(annotations=CHANGES_CAR)
def set_cabin_temperature(temperature_c: float) -> dict:
    """Turn on climate control and set the cabin temperature in °C (allowed 16-28)."""
    try:
        return vehicle.set_cabin_temperature(temperature_c)
    except VehicleError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def get_route_info(destination: str) -> dict:
    """Get the driving distance from the car's location (Munich) to a destination city."""
    try:
        key = normalise_destination(destination)
    except VehicleError as exc:
        raise ValueError(str(exc)) from exc
    status = vehicle.status()
    return {
        "destination": key.title(),
        "distance_km": DESTINATIONS_KM[key],
        "current_range_km": status["range_km"],
        "reachable_without_charging": status["range_km"] >= DESTINATIONS_KM[key],
        "charging_stations_on_route": len(CHARGING_STATIONS.get(key, [])),
    }


@mcp.tool(annotations=READ_ONLY)
def find_charging_stations(destination: str, min_power_kw: int = 50) -> list[dict]:
    """List available charging stations along the route to a destination. `km` is the distance from Munich along the route."""
    try:
        return stations_on_route(destination, min_power_kw=min_power_kw)
    except VehicleError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool(annotations=CHANGES_CAR)
def start_navigation(destination: str, charging_stop_ids: list[str] | None = None) -> dict:
    """Start route guidance to a destination, optionally via charging stops (use `station_id` values from a charging plan)."""
    try:
        return vehicle.start_navigation(destination, charging_stop_ids)
    except VehicleError as exc:
        raise ValueError(str(exc)) from exc
