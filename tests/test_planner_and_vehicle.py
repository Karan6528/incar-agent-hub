import pytest

from app.charging_agent import parse_request, plan_charging
from app.vehicle import Vehicle, VehicleError


def test_berlin_needs_two_stops_and_arrives_with_buffer():
    plan = plan_charging("Berlin", battery_pct=34)
    assert plan["feasible"] and plan["needs_charging"]
    assert [s["station_id"] for s in plan["stops"]] == ["ion-ingolstadt", "ion-leipzig"]
    assert plan["arrival_battery_pct"] >= 10
    # every stop is reached with at least the 10 % reserve
    assert all(s["arrive_battery_pct"] >= 10 for s in plan["stops"])


def test_no_charging_when_battery_is_enough():
    plan = plan_charging("Stuttgart", battery_pct=90)
    assert plan["feasible"] and not plan["needs_charging"] and plan["stops"] == []


def test_skips_stations_without_free_connectors():
    plan = plan_charging("Berlin", battery_pct=34)
    assert "tes-hof" not in [s["station_id"] for s in plan["stops"]]


def test_infeasible_when_no_fast_charger_in_reach():
    plan = plan_charging("Berlin", battery_pct=15, min_power_kw=150)
    assert plan["feasible"] is False


def test_unknown_destination_is_rejected():
    with pytest.raises(VehicleError):
        plan_charging("Paris", battery_pct=50)


def test_parse_text_request():
    assert parse_request("Plan charging to Berlin with 34%", []) == {"destination": "Berlin", "battery_pct": 34.0}


def test_temperature_limits():
    car = Vehicle()
    assert car.set_cabin_temperature(21.5)["cabin_temp_c"] == 21.5
    with pytest.raises(VehicleError):
        car.set_cabin_temperature(35)
