import pytest
from fastapi.testclient import TestClient
from datetime import date, time, datetime, timedelta
import uuid
import random
import string
from unittest.mock import AsyncMock, patch, MagicMock

from main import app

# Create a test client
client = TestClient(app)

# Properly mock db module to bypass startup and shutdown events
@pytest.fixture(autouse=True)
def mock_db():
    """Mock the db module to avoid real database connections"""
    with patch("app.db.database.db") as mock:
        # Create a mock pool
        mock.pool = MagicMock()
        
        # Set up necessary mock returns
        mock.connect = AsyncMock()
        mock.disconnect = AsyncMock()
        mock.get_tables = AsyncMock(return_value=[])
        mock.get_table_by_id = AsyncMock(return_value=None)
        mock.create_table = AsyncMock()
        mock.get_hours = AsyncMock(return_value=[])
        
        # Set up a mock table for testing
        table_id = uuid.uuid4()
        mock_table = {
            "id": table_id,
            "table_number": "T1",
            "min_capacity": 2,
            "max_capacity": 4,
            "is_shared": False,
            "location": "Main Floor",
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        # Configure mock to return specific table
        mock.get_table_by_id.return_value = mock_table
        mock.create_table.return_value = mock_table
        mock.get_tables.return_value = [mock_table]
        
        # Set up health check and restaurant hours
        mock.get_hours.return_value = [{
            "id": uuid.uuid4(),
            "day_of_week": 0,
            "open_time": time(9, 0),
            "close_time": time(22, 0),
            "last_reservation_time": time(21, 0)
        }]
        
        # Apply all other mocks to prevent DB access
        mock.is_valid_reservation_time = AsyncMock(return_value=True)
        mock.get_available_tables = AsyncMock(return_value=[])
        mock.create_reservation = AsyncMock(return_value={})
        mock.get_reservation_by_id = AsyncMock(return_value={})
        mock.update_reservation = AsyncMock(return_value={})
        mock.delete_reservation = AsyncMock(return_value=True)
        mock.delete_table = AsyncMock(return_value=True)
        mock.update_table = AsyncMock(return_value=mock_table)
        mock.update_reservation_status = AsyncMock(return_value={})
        mock.is_valid_reservation_time = AsyncMock(return_value=True)
        
        yield mock

# Test health check endpoint
def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

# Test get tables endpoint
def test_get_tables():
    response = client.get("/tables")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["table_number"] == "T1"

# Test get single table endpoint
def test_get_table():
    # Use a random UUID that will be mapped to our mock table
    test_id = str(uuid.uuid4())
    response = client.get(f"/tables/{test_id}")
    assert response.status_code == 200
    assert response.json()["table_number"] == "T1"

# Test create table endpoint
def test_create_table():
    new_table = {
        "table_number": "T2",
        "min_capacity": 2,
        "max_capacity": 4,
        "is_shared": False,
        "location": "Patio"
    }
    response = client.post("/tables", json=new_table)
    assert response.status_code == 200
    assert response.json()["table_number"] == "T1"  # Our mock always returns T1

# Test get restaurant hours
def test_get_restaurant_hours():
    response = client.get("/hours")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["day_of_week"] == 0

# Test validation error handling
def test_invalid_table_data():
    invalid_table = {
        "table_number": "T3",
        "min_capacity": 4,
        "max_capacity": 2,  # Invalid: max < min
        "is_shared": False
    }
    response = client.post("/tables", json=invalid_table)
    assert response.status_code == 422  # Validation error