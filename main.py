from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Dict, Any
from uuid import UUID
import time as pytime
from datetime import date, time, datetime
import uvicorn
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
import logging

from app.db.database import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        # logging.FileHandler("rezzy_api.log")
    ]
)

logger = logging.getLogger("rezzy")

# Pydantic models for request/response validation
class TableBase(BaseModel):
    table_number: str
    min_capacity: int
    max_capacity: int
    is_shared: bool = False
    location: Optional[str] = None

    @field_validator('min_capacity')
    def min_capacity_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('min_capacity must be positive')
        return v
    
    @field_validator('max_capacity')
    def max_capacity_must_be_greater_than_min(cls, v, values):
        if 'min_capacity' in values.data and v < values.data['min_capacity']:
            raise ValueError('max_capacity must be greater than or equal to min_capacity')
        return v

class TableCreate(TableBase):
    pass

class TableResponse(TableBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class CustomerBase(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None

    @field_validator('email', 'phone')
    def email_or_phone_required(cls, v, info):
        field_name = info.field_name
        if field_name == 'phone' and not v:
            email = info.data.get('email')
            if not email:
                raise ValueError('Either email or phone must be provided')
        return v

class CustomerResponse(CustomerBase):
    id: UUID

    class Config:
        from_attributes = True

class ReservationBase(BaseModel):
    party_size: int
    reservation_date: date
    start_time: time
    duration_minutes: int = 90
    notes: Optional[str] = None
    status: str = "pending"

    @field_validator('party_size')
    def party_size_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('party_size must be positive')
        return v

    @field_validator('status')
    def status_must_be_valid(cls, v):
        valid_statuses = ["pending", "confirmed", "seated", "completed", "cancelled", "no_show"]
        if v not in valid_statuses:
            raise ValueError(f'status must be one of {", ".join(valid_statuses)}')
        return v

class ReservationCreate(ReservationBase):
    customer: CustomerBase
    table_ids: List[UUID]

class ReservationUpdate(BaseModel):
    party_size: Optional[int] = None
    reservation_date: Optional[date] = None
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    table_ids: Optional[List[UUID]] = None

    @field_validator('party_size')
    def party_size_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError('party_size must be positive')
        return v

    @field_validator('status')
    def status_must_be_valid(cls, v):
        if v is not None:
            valid_statuses = ["pending", "confirmed", "seated", "completed", "cancelled", "no_show"]
            if v not in valid_statuses:
                raise ValueError(f'status must be one of {", ".join(valid_statuses)}')
        return v

class ReservationResponse(BaseModel):
    id: UUID
    party_size: int
    reservation_date: date
    start_time: time
    duration_minutes: int
    notes: Optional[str]
    status: str
    created_at: datetime
    customer_id: UUID
    customer_name: str
    customer_email: Optional[str]
    customer_phone: Optional[str]
    tables: List[Dict[str, Any]]

    class Config:
        from_attributes = True

class RestaurantHoursBase(BaseModel):
    day_of_week: int
    open_time: time
    close_time: time
    last_reservation_time: time

    @field_validator('day_of_week')
    def day_of_week_must_be_valid(cls, v):
        if v < 0 or v > 6:
            raise ValueError('day_of_week must be between 0 and 6 (Monday=0, Sunday=6)')
        return v

    @field_validator('close_time')
    def close_time_must_be_after_open(cls, v, values):
        if 'open_time' in values.data and v <= values.data['open_time']:
            raise ValueError('close_time must be after open_time')
        return v

    @field_validator('last_reservation_time')
    def last_reservation_time_must_be_valid(cls, v, values):
        if 'close_time' in values.data and v >= values.data['close_time']:
            raise ValueError('last_reservation_time must be before close_time')
        if 'open_time' in values.data and v <= values.data['open_time']:
            raise ValueError('last_reservation_time must be after open_time')
        return v

class RestaurantHoursResponse(RestaurantHoursBase):
    id: UUID

    class Config:
        from_attributes = True

class AvailabilityRequest(BaseModel):
    party_size: int
    reservation_date: date
    start_time: time
    duration_minutes: int = 90

class AvailabilityResponse(BaseModel):
    available_tables: List[Dict[str, Any]]
    is_valid_time: bool

class SpecialHoursBase(BaseModel):
    date: date
    name: str
    description: Optional[str] = None
    is_closed: bool = False
    open_time: Optional[time] = None
    close_time: Optional[time] = None
    last_reservation_time: Optional[time] = None
    
    @field_validator('open_time', 'close_time', 'last_reservation_time')
    def times_required_if_open(cls, v, info):
        is_closed = info.data.get('is_closed', False)
        if not is_closed and v is None:
            field_name = info.field_name
            raise ValueError(f"{field_name} is required when restaurant is open")
        return v
    
    @field_validator('close_time')
    def close_time_must_be_after_open(cls, v, values):
        if v is None:
            return v
        
        is_closed = values.data.get('is_closed', False)
        if is_closed:
            return v
            
        open_time = values.data.get('open_time')
        if open_time and v <= open_time:
            raise ValueError('close_time must be after open_time')
        return v
    
    @field_validator('last_reservation_time')
    def last_reservation_time_must_be_valid(cls, v, values):
        if v is None:
            return v
            
        is_closed = values.data.get('is_closed', False)
        if is_closed:
            return v
            
        open_time = values.data.get('open_time')
        close_time = values.data.get('close_time')
        
        if open_time and v <= open_time:
            raise ValueError('last_reservation_time must be after open_time')
            
        if close_time and v >= close_time:
            raise ValueError('last_reservation_time must be before close_time')
            
        return v

class SpecialHoursCreate(SpecialHoursBase):
    pass

class SpecialHoursResponse(SpecialHoursBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Define lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - runs before the app starts
    await db.connect()
    yield
    # Shutdown - runs when the app stops
    await db.disconnect()

# Create FastAPI app
app = FastAPI(
    title="Restaurant Reservation API",
    description="API for managing restaurant tables and reservations",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = pytime.time()
    
    # Get request details
    path = request.url.path
    query_params = str(request.query_params)
    client_host = request.client.host if request.client else "unknown"
    method = request.method
    
    # Log request
    logger.info(f"Request: {method} {path} - Params: {query_params} - Client: {client_host}")
    
    # Process request
    try:
        response = await call_next(request)
        
        # Log response
        process_time = pytime.time() - start_time
        logger.info(f"Response: {method} {path} - Status: {response.status_code} - Time: {process_time:.3f}s")
        
        return response
    except Exception as e:
        logger.error(f"Error handling request {method} {path}: {str(e)}")
        raise

# Tables API
@app.get("/tables", response_model=List[TableResponse])
async def get_tables(
    min_capacity: Optional[int] = None,
    max_capacity: Optional[int] = None,
    is_shared: Optional[bool] = None,
    location: Optional[str] = None
):
    """Get all tables with optional filtering."""
    filters = {}
    if min_capacity is not None:
        filters['min_capacity'] = min_capacity
    if max_capacity is not None:
        filters['max_capacity'] = max_capacity
    if is_shared is not None:
        filters['is_shared'] = is_shared
    if location is not None:
        filters['location'] = location
        
    return await db.get_tables(filters)

@app.get("/tables/{table_id}", response_model=TableResponse)
async def get_table(table_id: UUID):
    """Get a table by ID."""
    table = await db.get_table_by_id(table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return table

@app.post("/tables", response_model=TableResponse)
async def create_table(table: TableCreate):
    """Create a new table."""
    return await db.create_table(table.dict())

@app.put("/tables/{table_id}", response_model=TableResponse)
async def update_table(table_id: UUID, table: TableBase):
    """Update a table."""
    updated = await db.update_table(table_id, table.dict())
    if not updated:
        raise HTTPException(status_code=404, detail="Table not found")
    return updated

@app.delete("/tables/{table_id}")
async def delete_table(table_id: UUID):
    """Delete a table."""
    success = await db.delete_table(table_id)
    if not success:
        raise HTTPException(status_code=404, detail="Table not found")
    return {"status": "success", "message": "Table deleted"}

# Reservations API
@app.get("/reservations", response_model=List[ReservationResponse])
async def get_reservations(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    table_id: Optional[UUID] = None,
    status: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """Get reservations with optional filtering."""
    filters = {}
    if date_from is not None:
        filters['date_from'] = date_from
    if date_to is not None:
        filters['date_to'] = date_to
    if table_id is not None:
        filters['table_id'] = table_id
    if status is not None:
        filters['status'] = status
    if customer_id is not None:
        filters['customer_id'] = customer_id
        
    return await db.get_reservations(filters, limit, offset)

@app.get("/reservations/{reservation_id}", response_model=ReservationResponse)
async def get_reservation(reservation_id: UUID):
    """Get a reservation by ID."""
    reservation = await db.get_reservation_by_id(reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation

@app.post("/reservations", response_model=ReservationResponse)
async def create_reservation(reservation: ReservationCreate):
    """Create a new reservation."""
    
    # Check if reservation time is valid
    is_valid = await db.is_valid_reservation_time(
        reservation.reservation_date,
        reservation.start_time,
        reservation.duration_minutes
    )
    
    if not is_valid:
        raise HTTPException(
            status_code=400, 
            detail="Reservation time is outside restaurant operating hours"
        )
    
    # Check table availability
    available_tables = await db.get_available_tables(
        reservation.party_size,
        reservation.reservation_date,
        reservation.start_time,
        reservation.duration_minutes
    )
    
    # Verify all requested tables are available
    available_table_ids = [str(table["id"]) for table in available_tables]
    
    for table_id in reservation.table_ids:
        if str(table_id) not in available_table_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Table {table_id} is not available for the requested time"
            )
    
    # Create the reservation
    return await db.create_reservation(
        reservation.customer.dict(),
        reservation.dict(exclude={"customer", "table_ids"}),
        reservation.table_ids
    )

@app.put("/reservations/{reservation_id}", response_model=ReservationResponse)
async def update_reservation(reservation_id: UUID, reservation: ReservationUpdate):
    """Update a reservation."""
    
    # Get current reservation
    current = await db.get_reservation_by_id(reservation_id)
    if not current:
        raise HTTPException(status_code=404, detail="Reservation not found")
    
    update_data = reservation.dict(exclude_unset=True)
    table_ids = update_data.pop("table_ids", None)
    
    # If changing time, date, or duration, check if the new time is valid
    time_change = any(key in update_data for key in ["reservation_date", "start_time", "duration_minutes"])
    
    if time_change:
        # Use new values if provided, otherwise use current values
        check_date = update_data.get("reservation_date", current["reservation_date"])
        check_time = update_data.get("start_time", current["start_time"])
        check_duration = update_data.get("duration_minutes", current["duration_minutes"])
        
        # Check if new time is valid
        is_valid = await db.is_valid_reservation_time(
            check_date, check_time, check_duration
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400, 
                detail="Updated reservation time is outside restaurant operating hours"
            )
        
        # Check table availability if tables are being changed or time is changing
        if table_ids is not None or time_change:
            party_size = update_data.get("party_size", current["party_size"])
            
            available_tables = await db.get_available_tables(
                party_size, check_date, check_time, check_duration
            )
            
            # If updating tables, verify all requested tables are available
            if table_ids is not None:
                available_table_ids = [str(table["id"]) for table in available_tables]
                
                # Add current tables to available tables (since they're already assigned to this reservation)
                current_table_ids = [str(table["id"]) for table in current["tables"]]
                available_table_ids.extend(current_table_ids)
                
                # Remove duplicates
                available_table_ids = list(set(available_table_ids))
                
                for table_id in table_ids:
                    if str(table_id) not in available_table_ids:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Table {table_id} is not available for the requested time"
                        )
    
    # Update the reservation
    updated = await db.update_reservation(
        reservation_id,
        update_data,
        table_ids
    )
    
    if not updated:
        raise HTTPException(status_code=404, detail="Reservation not found")
    
    return updated

@app.patch("/reservations/{reservation_id}/status", response_model=ReservationResponse)
async def update_reservation_status(
    reservation_id: UUID, 
    status: str = Query(..., description="New status")
):
    """Update a reservation's status."""
    # Validate status
    valid_statuses = ["pending", "confirmed", "seated", "completed", "cancelled", "no_show"]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    updated = await db.update_reservation_status(reservation_id, status)
    if not updated:
        raise HTTPException(status_code=404, detail="Reservation not found")
    
    return updated

@app.delete("/reservations/{reservation_id}")
async def delete_reservation(reservation_id: UUID):
    """Delete a reservation."""
    success = await db.delete_reservation(reservation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {"status": "success", "message": "Reservation deleted"}

# Availability API
@app.post("/availability", response_model=AvailabilityResponse)
async def check_availability(request: AvailabilityRequest):
    """Check table availability for a given time and party size."""
    # Check if reservation time is valid
    is_valid = await db.is_valid_reservation_time(
        request.reservation_date,
        request.start_time,
        request.duration_minutes
    )
    
    # Get available tables
    available_tables = []
    if is_valid:
        available_tables = await db.get_available_tables(
            request.party_size,
            request.reservation_date,
            request.start_time,
            request.duration_minutes
        )
    
    return {
        "available_tables": available_tables,
        "is_valid_time": is_valid
    }

# Restaurant Hours API
@app.get("/hours", response_model=List[RestaurantHoursResponse])
async def get_restaurant_hours():
    """Get restaurant operating hours."""
    return await db.get_hours()

@app.put("/hours", response_model=RestaurantHoursResponse)
async def set_restaurant_hours(hours: RestaurantHoursBase):
    """Set restaurant operating hours for a specific day."""
    return await db.set_hours(
        hours.day_of_week,
        hours.open_time,
        hours.close_time,
        hours.last_reservation_time
    )

@app.get("/special-hours", response_model=List[SpecialHoursResponse])
async def get_special_hours(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None
):
    """Get all special hours with optional date range filtering."""
    return await db.get_special_hours(date_from, date_to)

@app.get("/special-hours/{date_str}", response_model=Optional[SpecialHoursResponse])
async def get_special_hours_by_date(date_str: str):
    """Get special hours for a specific date."""
    try:
        year, month, day = map(int, date_str.split('-'))
        date_val = date(year, month, day)
        special_hours = await db.get_special_hours_by_date(date_val)
        if not special_hours:
            return None
        return special_hours
    except ValueError as e:
        logger.error(f"Error parsing date: {date_str}, error: {str(e)}")
        return None

@app.put("/special-hours", response_model=SpecialHoursResponse)
async def set_special_hours(special_hours: SpecialHoursCreate):
    """Set special hours for a date (holiday or event)."""
    return await db.set_special_hours(
        special_hours.date,
        special_hours.name,
        special_hours.description,
        special_hours.is_closed,
        special_hours.open_time,
        special_hours.close_time,
        special_hours.last_reservation_time
    )

@app.delete("/special-hours/{special_hours_id}")
async def delete_special_hours(special_hours_id: UUID):
    """Delete special hours."""
    success = await db.delete_special_hours(special_hours_id)
    if not success:
        raise HTTPException(status_code=404, detail="Special hours not found")
    return {"status": "success", "message": "Special hours deleted"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)