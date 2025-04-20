import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from datetime import date, time, datetime, timedelta
import uuid
import json
import asyncio

# Import the actual app and db connection
from main import app
from app.db.database import db

# Use a separate client instance for integration tests
client = TestClient(app)

# Proper setup fixtures for pytest-asyncio
@pytest_asyncio.fixture(scope="module")
async def setup_db():
    """Connect to the database before tests and disconnect after"""
    # Create a new connection pool for testing
    await db.connect()
    yield
    await db.disconnect()

# Run each test with the database connection
@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_db")
async def test_create_and_get_table():
    """Test creating a table and then retrieving it"""
    # Create a new table
    new_table_data = {
        "table_number": f"INT-{uuid.uuid4().hex[:6]}",  # Generate unique table number
        "min_capacity": 2,
        "max_capacity": 4,
        "is_shared": False,
        "location": "Integration Test Area"
    }
    
    response = client.post("/tables", json=new_table_data)
    assert response.status_code == 200
    created_table = response.json()
    
    # Verify the table was created correctly
    assert created_table["table_number"] == new_table_data["table_number"]
    assert created_table["min_capacity"] == new_table_data["min_capacity"]
    assert created_table["max_capacity"] == new_table_data["max_capacity"]
    
    # Get the table by ID
    table_id = created_table["id"]
    response = client.get(f"/tables/{table_id}")
    assert response.status_code == 200
    retrieved_table = response.json()
    
    # Verify it's the same table
    assert retrieved_table["id"] == table_id
    assert retrieved_table["table_number"] == new_table_data["table_number"]
    
    # Clean up - delete the table
    response = client.delete(f"/tables/{table_id}")
    assert response.status_code == 200

@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_db") 
async def test_restaurant_hours():
    """Test retrieving restaurant hours"""
    response = client.get("/hours")
    assert response.status_code == 200
    hours = response.json()
    
    # Just verify we got a response, may be empty in test DB
    assert isinstance(hours, list)

@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_db")
async def test_full_reservation_flow():
    """Test the complete reservation flow"""
    # 1. Create a table
    table_data = {
        "table_number": f"RES-{uuid.uuid4().hex[:6]}",
        "min_capacity": 2,
        "max_capacity": 4,
        "is_shared": False,
        "location": "Reservation Test Area"
    }
    
    response = client.post("/tables", json=table_data)
    assert response.status_code == 200
    table = response.json()
    table_id = table["id"]
    
    # 2. Create a reservation for tomorrow
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    reservation_data = {
        "party_size": 3,
        "reservation_date": tomorrow,
        "start_time": "18:00:00",
        "duration_minutes": 90,
        "notes": "Integration test reservation",
        "status": "pending",
        "customer": {
            "name": "Test Customer",
            "email": "test@example.com",
            "phone": "555-123-4567",
            "notes": "Test customer notes"
        },
        "table_ids": [table_id]
    }
    
    response = client.post("/reservations", json=reservation_data)
    assert response.status_code == 200
    reservation = response.json()
    reservation_id = reservation["id"]
    
    # 3. Verify reservation details
    assert reservation["party_size"] == reservation_data["party_size"]
    assert reservation["status"] == "pending"
    assert reservation["customer_name"] == "Test Customer"
    assert len(reservation["tables"]) == 1
    assert reservation["tables"][0]["id"] == table_id
    
    # 4. Update reservation status
    response = client.patch(f"/reservations/{reservation_id}/status?status=confirmed")
    assert response.status_code == 200
    updated = response.json()
    assert updated["status"] == "confirmed"
    
    # 5. Get all reservations for tomorrow
    response = client.get(f"/reservations?date_from={tomorrow}&date_to={tomorrow}")
    assert response.status_code == 200
    reservations = response.json()
    assert len(reservations) >= 1
    found = False
    for res in reservations:
        if res["id"] == reservation_id:
            found = True
            break
    assert found, "Created reservation not found in list"
    
    # 6. Delete the reservation
    response = client.delete(f"/reservations/{reservation_id}")
    assert response.status_code == 200
    
    # 7. Delete the table
    response = client.delete(f"/tables/{table_id}")
    assert response.status_code == 200

@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_db")
async def test_availability_check():
    """Test checking table availability"""
    # 1. Create a table
    table_data = {
        "table_number": f"AVL-{uuid.uuid4().hex[:6]}",
        "min_capacity": 2,
        "max_capacity": 4,
        "is_shared": False,
        "location": "Availability Test Area"
    }
    
    response = client.post("/tables", json=table_data)
    assert response.status_code == 200
    table = response.json()
    table_id = table["id"]
    
    # 2. Check availability for tomorrow
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    availability_request = {
        "party_size": 3,
        "reservation_date": tomorrow,
        "start_time": "18:00:00",
        "duration_minutes": 90
    }
    
    response = client.post("/availability", json=availability_request)
    assert response.status_code == 200
    result = response.json()
    
    # 3. Clean up - delete the table
    response = client.delete(f"/tables/{table_id}")
    assert response.status_code == 200