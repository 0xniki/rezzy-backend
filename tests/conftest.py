import pytest
import pytest_asyncio
import os
import asyncpg
import asyncio
from datetime import datetime, time
import uuid

# Setup test environment variables
@pytest.fixture(scope="session", autouse=True)
def set_test_env():
    """Set environment variables for testing"""
    os.environ["DB_USER"] = "postgres"
    os.environ["DB_PASSWORD"] = "password"
    os.environ["DB_HOST"] = "localhost"
    os.environ["DB_PORT"] = "5432"
    os.environ["DB_NAME"] = "rezzy_test"  # Use a test database
    yield
    # No need to clean up env vars as they're session-scoped

# Function to create test database
@pytest_asyncio.fixture(scope="session")
async def create_test_db():
    """Create a test database if it doesn't exist"""
    conn = None
    try:
        conn = await asyncpg.connect(
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "password"),
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            database="postgres"  # Connect to default db first
        )
        
        # Check if database exists
        test_db = os.environ.get("DB_NAME", "rezzy_test")
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", test_db
        )
        
        if not exists:
            await conn.execute(f'CREATE DATABASE {test_db}')
            print(f"Created test database: {test_db}")
        else:
            print(f"Using existing test database: {test_db}")
        
    except Exception as e:
        print(f"Error setting up test database: {e}")
        raise
    finally:
        if conn:
            await conn.close()
    
    # Now connect to the test database to initialize schema
    try:
        conn = await asyncpg.connect(
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "password"),
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            database=test_db
        )
        
        # Create tables if needed
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS tables (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            table_number VARCHAR(50) NOT NULL UNIQUE,
            min_capacity INTEGER NOT NULL,
            max_capacity INTEGER NOT NULL,
            is_shared BOOLEAN NOT NULL DEFAULT false,
            location VARCHAR(100),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS customers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(200) NOT NULL,
            email VARCHAR(200),
            phone VARCHAR(50),
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS chairs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            table_id UUID NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            is_assigned BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS reservations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            party_size INTEGER NOT NULL,
            reservation_date DATE NOT NULL,
            start_time TIME NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 90,
            notes TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        CREATE TABLE IF NOT EXISTS table_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reservation_id UUID NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
            table_id UUID NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            UNIQUE(reservation_id, table_id)
        );
        
        CREATE TABLE IF NOT EXISTS restaurant_hours (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            day_of_week INTEGER NOT NULL,
            open_time TIME NOT NULL,
            close_time TIME NOT NULL,
            last_reservation_time TIME NOT NULL,
            UNIQUE(day_of_week)
        );
        ''')
        
        # Insert some default hours
        await conn.execute('''
        INSERT INTO restaurant_hours (day_of_week, open_time, close_time, last_reservation_time)
        VALUES 
            (0, '09:00', '22:00', '21:00'),
            (1, '09:00', '22:00', '21:00'),
            (2, '09:00', '22:00', '21:00'),
            (3, '09:00', '22:00', '21:00'),
            (4, '09:00', '23:00', '22:00'),
            (5, '09:00', '23:00', '22:00'),
            (6, '09:00', '22:00', '21:00')
        ON CONFLICT (day_of_week) DO NOTHING;
        ''')
        
        print("Test database schema initialized")
        
    except Exception as e:
        print(f"Error initializing test database schema: {e}")
        raise
    finally:
        if conn:
            await conn.close()
    
    yield