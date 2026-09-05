from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime


class StationReading(BaseModel):
    """
    Flexible model for station readings with dynamic field support.
    All sensor fields are optional to handle heterogeneous schemas.
    """
    station_name: str
    timestamp: datetime
    unit: Optional[str] = None
    
    # Intake Air Parameters
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    velocity: Optional[float] = None
    
    # Outtake Air Parameters
    outtake_unit: Optional[str] = None
    outtake_humidity: Optional[float] = None
    outtake_velocity: Optional[float] = None
    outtake_temperature: Optional[float] = None
    
    # Water Production Parameters
    flow_lmin: Optional[float] = None
    flow_hz: Optional[float] = None
    flow_total: Optional[float] = None
    weight: Optional[float] = None
    
    # Power Parameters
    power: Optional[float] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    energy: Optional[float] = None
    
    # System Status
    pump_status: Optional[Any] = None  # Can be int (0/1) or string ("ON"/"OFF")
    
    class Config:
        json_schema_extra = {
            "example": {
                "station_name": "test-station-1",
                "timestamp": "2025-11-01T00:00:00Z",
                "unit": "AguaPars",
                "temperature": 28.5,
                "humidity": 45.2,
                "velocity": 2.3,
                "outtake_unit": "AguaPars",
                "outtake_humidity": 68.5,
                "outtake_velocity": 1.8,
                "flow_lmin": 5.8,
                "flow_hz": 58.2,
                "weight": 150.5,
                "power": 850.2,
                "energy": 425.1,
                "pump_status": "ON"
            }
        }


class StationMetadata(BaseModel):
    """Metadata describing available fields for a station"""
    station_name: str
    available_fields: List[str]
    field_groups: Dict[str, List[str]]
    last_reading: Optional[datetime] = None
    total_readings: int = 0
    units: Dict[str, str] = Field(default_factory=dict)


class StationInfo(BaseModel):
    """Station configuration and status"""
    station_name: str
    unit: str
    location: Optional[str] = None
    status: str = "active"
    metadata: StationMetadata
    display_name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    hidden: bool = False


class StationAdminUpdate(BaseModel):
    """Partial update for admin-editable station fields — only fields that
    are actually provided get written to Firestore."""
    display_name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    hidden: Optional[bool] = None


class StationRegistryItem(BaseModel):
    """Lightweight registry entry for a station (used by RPi UI and validation)"""
    station_name: str
    location: Optional[str] = None
    status: str  # ACTIVE, INACTIVE, PENDING


class StationRegistryResponse(BaseModel):
    """Response for /stations-registry endpoint"""
    stations: List[StationRegistryItem]
    total: int


class CreateStationRequest(BaseModel):
    """Request body for registering a new station"""
    station_name: str
    location: Optional[str] = None


class StationImpact(BaseModel):
    """Lifetime water-harvested total for one station, precomputed offline"""
    station_name: str
    location: Optional[str] = None
    total_liters: float
    readings_processed: int
    updated_at: Optional[datetime] = None
    hidden: bool = False


class ImpactResponse(BaseModel):
    """Response for /impact — aggregate real-world harvesting numbers"""
    total_liters: float
    stations: List[StationImpact]
    updated_at: Optional[datetime] = None


class ReadingsQueryParams(BaseModel):
    """Query parameters for filtering readings"""
    station_name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    fields: Optional[List[str]] = None
    limit: int = Field(default=100, le=10000)
    offset: int = Field(default=0, ge=0)


class ReadingsResponse(BaseModel):
    """Paginated response for readings"""
    data: List[StationReading]
    total: int
    limit: int
    offset: int
    metadata: Optional[StationMetadata] = None


class BulkExportRequest(BaseModel):
    """Request for bulk data export"""
    station_names: Optional[List[str]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    fields: Optional[List[str]] = None
    format: str = Field(default="csv", pattern="^(csv|json|parquet)$")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: datetime
    services: Dict[str, str]
