"""Data models and schemas for AutoRoster entities."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class EmployeeRecord(BaseModel):
    employee_id: str = ""
    employee_name: str = ""
    grade: str = ""
    shift: str = "A"
    pickup_point: str = ""
    mobile: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_status: str = "UNVERIFIED"
    resolution_reason: Optional[str] = None
    corridor: Optional[str] = None
    assigned_bus: Optional[str] = None
    seq: Optional[int] = None

class PhysicalStop(BaseModel):
    stop_name: str
    lat: float
    lon: float
    demand: int = 1
    service_minutes: int = 1
    corridor: str = "Local Vicinity"
    distance_to_destination_km: float = 0.0
    employees: List[Dict[str, Any]] = Field(default_factory=list)

class RouteStop(BaseModel):
    seq: int
    stop_name: str
    lat: float
    lon: float
    distance_from_prev_km: float = 0.0
    cumulative_km: float = 0.0
    passengers_boarding: int = 0
    cumulative_passengers: int = 0
    distance_to_destination_km: float = 0.0
    ride_minutes_to_destination: float = 0.0
    ride_time_status: str = "ON_TARGET"
    employees: List[Dict[str, Any]] = Field(default_factory=list)

class OptimizedRoute(BaseModel):
    bus_id: str
    shift: str
    corridor: str
    passengers_assigned: int
    capacity: int = 32
    seats_available: int = 0
    total_stops: int
    total_km: float
    road_km: float
    total_minutes: float
    longest_ride_minutes: float
    ride_time_status: str
    destination_name: str
    stops: List[RouteStop]
