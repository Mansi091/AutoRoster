"""Pydantic schemas for API request and response validation."""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class OptimizeRequest(BaseModel):
    employees: List[Dict[str, Any]] = Field(default_factory=list)
    shift_filter: Optional[str] = None
    bus_capacity: Optional[int] = 32
    destination: Optional[Dict[str, Any]] = None

class ResolveSingleRequest(BaseModel):
    stop_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ai_resolve: bool = False

class ExportRequest(BaseModel):
    routes: List[Dict[str, Any]] = Field(default_factory=list)
    employees: List[Dict[str, Any]] = Field(default_factory=list)
    unverified_employees: List[Dict[str, Any]] = Field(default_factory=list)
