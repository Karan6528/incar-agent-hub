"""Charging planner agent (A2A).

Separate agent that only does one thing: plan charging stops for a trip.
Uses the a2a-sdk so it gets an agent card + JSON-RPC endpoint for free.
"""

from __future__ import annotations

import re

from a2a.helpers import get_data_parts, get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    APIKeySecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from app.vehicle import (
    BATTERY_CAPACITY_KWH,
    FULL_RANGE_KM,
    VehicleError,
    normalise_destination,
    route_distance_km,
    stations_on_route,
)

A2A_PATH = "/a2a/charging"
CAR_MAX_CHARGE_KW = 195  # car can't take more than this even at a 350kW charger
AVG_CHARGE_EFFICIENCY = 0.7  # rough average, charging gets slower above ~60%


# Planning logic (pure function, easy to unit-test)
def plan_charging(
    destination: str,
    battery_pct: float,
    min_power_kw: int = 150,
    reserve_pct: float = 10,
    target_pct: float = 80,
    arrival_buffer_pct: float = 20,
) -> dict:
    # Greedy: go to the furthest fast charger we can reach (keeping a reserve),
    # charge to 80%, repeat. At the last stop only charge what's needed to arrive
    # with ~20% left.
    if not 0 <= battery_pct <= 100:
        raise VehicleError("battery_pct must be between 0 and 100.")
    key = normalise_destination(destination)
    distance = route_distance_km(key)
    stations = sorted(stations_on_route(key, min_power_kw=min_power_kw), key=lambda s: s["km"])

    pos, soc, stops = 0.0, float(battery_pct), []
    pct_per_km = 100 / FULL_RANGE_KM

    while pos + (soc - reserve_pct) / pct_per_km < distance:
        reachable = pos + (soc - reserve_pct) / pct_per_km
        candidates = [s for s in stations if pos < s["km"] <= reachable]
        if not candidates:
            return {
                "destination": key.title(),
                "distance_km": distance,
                "start_battery_pct": battery_pct,
                "feasible": False,
                "needs_charging": True,
                "stops": stops,
                "summary": (
                    f"No available fast charger (≥{min_power_kw} kW) is in reach after km {pos:.0f}. "
                    "Charge before departure or allow slower chargers."
                ),
            }
        station = candidates[-1]
        arrive = soc - (station["km"] - pos) * pct_per_km
        needed_for_rest = (distance - station["km"]) * pct_per_km + arrival_buffer_pct
        depart = min(target_pct, max(needed_for_rest, arrive))
        energy_kwh = (depart - arrive) / 100 * BATTERY_CAPACITY_KWH
        power = min(station["power_kw"], CAR_MAX_CHARGE_KW) * AVG_CHARGE_EFFICIENCY
        stops.append({
            "station_id": station["id"],
            "name": station["name"],
            "km": station["km"],
            "power_kw": station["power_kw"],
            "arrive_battery_pct": round(arrive, 1),
            "depart_battery_pct": round(depart, 1),
            "charge_minutes": round(energy_kwh / power * 60),
        })
        pos, soc = station["km"], depart

    arrival = round(soc - (distance - pos) * pct_per_km, 1)
    total_minutes = sum(s["charge_minutes"] for s in stops)
    if stops:
        names = ", ".join(f"{s['name']} (~{s['charge_minutes']} min)" for s in stops)
        summary = (f"{len(stops)} charging stop(s) to {key.title()}: {names}. "
                   f"Arrive with about {arrival:.0f}% battery.")
    else:
        summary = f"No charging needed to reach {key.title()}; you arrive with about {arrival:.0f}% battery."
    return {
        "destination": key.title(),
        "distance_km": distance,
        "start_battery_pct": battery_pct,
        "feasible": True,
        "needs_charging": bool(stops),
        "stops": stops,
        "total_charge_minutes": total_minutes,
        "arrival_battery_pct": arrival,
        "summary": summary,
    }


def parse_request(text: str, data_parts: list) -> dict:
    # accepts either a data part or plain text like "to Berlin with 34%"
    for data in data_parts:
        if isinstance(data, dict) and "destination" in data:
            return data
    dest = re.search(r"\bto\s+([A-Za-zÄÖÜäöüß]+)", text)
    batt = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if not dest or not batt:
        raise VehicleError(
            "Send JSON data {\"destination\": ..., \"battery_pct\": ...} or text like 'Plan charging to Berlin with 34%'."
        )
    return {"destination": dest.group(1), "battery_pct": float(batt.group(1))}


# A2A agent executor
class ChargingPlannerExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        try:
            request = parse_request(get_message_text(context.message), get_data_parts(context.message.parts))
            allowed = {"destination", "battery_pct", "min_power_kw", "reserve_pct", "target_pct"}
            plan = plan_charging(**{k: v for k, v in request.items() if k in allowed})
        except (VehicleError, TypeError, ValueError) as exc:
            await updater.failed(updater.new_agent_message([new_text_part(str(exc))]))
            return
        await updater.add_artifact(
            [new_data_part(plan, media_type="application/json"), new_text_part(plan["summary"])],
            name="charging_plan",
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Charging Planner Agent",
        description="Plans EV charging stops for a route, given destination and current battery level.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(url=f"{base_url.rstrip('/')}{A2A_PATH}", protocol_binding="JSONRPC", protocol_version="1.0"),
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id="plan_charging",
                name="Plan charging stops",
                description="Returns the charging stops, charge times and arrival battery level for a trip from Munich.",
                tags=["ev", "charging", "route-planning"],
                examples=["Plan charging to Berlin with 34%", '{"destination": "Hamburg", "battery_pct": 60}'],
            )
        ],
        security_schemes={
            "apiKey": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(location="header", name="X-API-Key",
                                                             description="Shared API key")
            )
        },
        security_requirements=[SecurityRequirement(schemes={"apiKey": StringList(list=[])})],
    )
