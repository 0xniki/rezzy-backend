import os
import logging
import hashlib
from typing import Optional, List, Dict, Any, Union
import asyncpg
from asyncpg.connection import Connection
from asyncpg.pool import Pool
from datetime import datetime, date, time, timedelta
from uuid import UUID

class Database:
    """Database connection manager and query interface."""
    
    def __init__(self, dsn: Optional[str] = None):
        """Initialize the database connection manager.
        
        Args:
            dsn: Database connection string. If None, will be constructed from env vars.
        """
        self.pool: Optional[Pool] = None
        self._dsn = dsn or self._get_dsn_from_env()

        self.logger = logging.getLogger(__name__)
        
    @staticmethod
    def _get_dsn_from_env() -> str:
        """Construct a DSN from environment variables."""
        return f"postgres://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', 'password')}@" \
               f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/" \
               f"{os.getenv('DB_NAME', 'rezzy')}"
    
    async def connect(self) -> None:
        """Create a connection pool."""
        if not self.pool:
            try:
                self.pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=5,
                    max_size=20
                )
                self.logger.info("Database connection pool established")
            except Exception as e:
                self.logger.error(f"Error connecting to database: {e}")
                raise
    
    async def disconnect(self) -> None:
        """Close all connections in the pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            self.logger.info("Database connection pool closed")

    # ===================
    # Table Operations
    # ===================
    
    async def get_tables(self, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Get tables with optional filtering."""
        query = """
        SELECT id, table_number, min_capacity, max_capacity, 
               is_shared, location, created_at, updated_at
        FROM tables
        """
        
        values = []
        if filters:
            conditions = []
            for i, (key, value) in enumerate(filters.items(), 1):
                if key == 'min_capacity':
                    conditions.append(f"min_capacity >= ${i}")
                    values.append(value)
                elif key == 'max_capacity':
                    conditions.append(f"max_capacity >= ${i}")
                    values.append(value)
                elif key == 'is_shared':
                    conditions.append(f"is_shared = ${i}")
                    values.append(value)
                elif key == 'location':
                    conditions.append(f"location = ${i}")
                    values.append(value)
                    
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                
        query += " ORDER BY table_number"
        
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, *values)
            return [dict(r) for r in records]
    
    async def get_table_by_id(self, table_id: UUID) -> Dict[str, Any]:
        """Get a table by its ID."""
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow("""
            SELECT id, table_number, min_capacity, max_capacity, 
                   is_shared, location, created_at, updated_at
            FROM tables
            WHERE id = $1
            """, table_id)
            return dict(record) if record else None
    
    async def create_table(self, table_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new table."""
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow("""
            INSERT INTO tables (
                table_number, min_capacity, max_capacity, 
                is_shared, location
            ) VALUES ($1, $2, $3, $4, $5)
            RETURNING id, table_number, min_capacity, max_capacity, 
                      is_shared, location, created_at, updated_at
            """, 
            table_data['table_number'],
            table_data['min_capacity'],
            table_data['max_capacity'],
            table_data.get('is_shared', False),
            table_data.get('location')
            )
            
            # Create chairs for the table
            chair_count = table_data['max_capacity']
            for _ in range(chair_count):
                await conn.execute("""
                INSERT INTO chairs (table_id, is_assigned)
                VALUES ($1, true)
                """, record['id'])
                
            return dict(record)
    
    async def update_table(self, table_id: UUID, table_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a table."""
        fields = []
        values = []
        
        # Build dynamic update query
        for i, (key, value) in enumerate(table_data.items(), 1):
            if key in ['table_number', 'min_capacity', 'max_capacity', 'is_shared', 'location']:
                fields.append(f"{key} = ${i}")
                values.append(value)
        
        if not fields:
            raise ValueError("No valid fields to update")
        
        async with self.pool.acquire() as conn:
            # Update table
            query = f"""
            UPDATE tables 
            SET {', '.join(fields)}
            WHERE id = ${len(values) + 1}
            RETURNING id, table_number, min_capacity, max_capacity, 
                      is_shared, location, created_at, updated_at
            """
            values.append(table_id)
            
            record = await conn.fetchrow(query, *values)
            
            # Handle chair updates if max_capacity changed
            if 'max_capacity' in table_data:
                current_chairs = await conn.fetch(
                    "SELECT id FROM chairs WHERE table_id = $1", 
                    table_id
                )
                
                current_count = len(current_chairs)
                target_count = table_data['max_capacity']
                
                if current_count < target_count:
                    # Add more chairs
                    for _ in range(target_count - current_count):
                        await conn.execute("""
                        INSERT INTO chairs (table_id, is_assigned)
                        VALUES ($1, true)
                        """, table_id)
                elif current_count > target_count:
                    # Remove excess chairs (keeping the oldest ones)
                    chairs_to_remove = current_chairs[target_count:]
                    for chair in chairs_to_remove:
                        await conn.execute(
                            "DELETE FROM chairs WHERE id = $1", 
                            chair['id']
                        )
            
            return dict(record) if record else None
    
    async def delete_table(self, table_id: UUID) -> bool:
        """Delete a table (and its chairs due to CASCADE)."""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
            DELETE FROM tables WHERE id = $1
            """, table_id)
            return result == "DELETE 1"
    
    # ===================
    # Reservation Operations
    # ===================
    
    async def get_available_tables(
        self, 
        party_size: int,
        reservation_date: date,
        start_time: time,
        duration_minutes: int = 90
    ) -> List[Dict[str, Any]]:
        """Find available tables for a given party size and time slot."""
        # Calculate end time
        end_time_dt = datetime.combine(datetime.min, start_time) + timedelta(minutes=duration_minutes)
        end_time = end_time_dt.time()
        
        async with self.pool.acquire() as conn:
            # Get all tables that could fit the party size
            query = """
            WITH booked_tables AS (
                SELECT DISTINCT ta.table_id
                FROM reservations r
                JOIN table_assignments ta ON r.id = ta.reservation_id
                WHERE 
                    r.reservation_date = $1
                    AND r.status NOT IN ('cancelled', 'no_show')
                    AND (
                        -- Overlapping time ranges
                        (r.start_time <= $2 AND 
                         (r.start_time + make_interval(mins => r.duration_minutes)) > $2)
                        OR
                        (r.start_time < $3 AND 
                         (r.start_time + make_interval(mins => r.duration_minutes)) >= $3)
                        OR
                        ($2 <= r.start_time AND $3 >= 
                         (r.start_time + make_interval(mins => r.duration_minutes)))
                    )
            )
            SELECT 
                t.id, 
                t.table_number,
                t.min_capacity,
                t.max_capacity,
                t.is_shared,
                t.location,
                t.is_shared AND (t.max_capacity - COALESCE(SUM(r.party_size), 0)) >= $4 AS can_be_shared,
                t.max_capacity - COALESCE(SUM(r.party_size), 0) AS remaining_capacity
            FROM 
                tables t
            LEFT JOIN (
                -- For shared tables, get existing reservations during the time slot
                SELECT 
                    ta.table_id,
                    r.party_size
                FROM 
                    reservations r
                JOIN 
                    table_assignments ta ON r.id = ta.reservation_id
                WHERE 
                    r.reservation_date = $1
                    AND r.status NOT IN ('cancelled', 'no_show')
                    AND (
                        (r.start_time <= $2 AND 
                         (r.start_time + make_interval(mins => r.duration_minutes)) > $2)
                        OR
                        (r.start_time < $3 AND 
                         (r.start_time + make_interval(mins => r.duration_minutes)) >= $3)
                        OR
                        ($2 <= r.start_time AND $3 >= 
                         (r.start_time + make_interval(mins => r.duration_minutes)))
                    )
            ) AS r ON t.id = r.table_id AND t.is_shared = true
            WHERE 
                -- Exclude tables already fully booked
                (t.id NOT IN (SELECT table_id FROM booked_tables) OR t.is_shared = true)
                AND
                -- Must fit the party size
                t.min_capacity <= $4 AND t.max_capacity >= $4
            GROUP BY
                t.id, t.table_number, t.min_capacity, t.max_capacity, t.is_shared, t.location
            HAVING
                -- For shared tables, ensure enough remaining capacity
                (t.is_shared = false OR (t.max_capacity - COALESCE(SUM(r.party_size), 0)) >= $4)
            ORDER BY
                -- Order by most efficient use of space
                ABS(t.min_capacity - $4), t.table_number
            """
            
            records = await conn.fetch(
                query, 
                reservation_date, 
                start_time,
                end_time,
                party_size
            )
            return [dict(r) for r in records]
    
    async def create_reservation(
        self, 
        customer_data: Dict[str, Any],
        reservation_data: Dict[str, Any],
        table_ids: List[UUID]
    ) -> Dict[str, Any]:
        """Create a reservation with customer info and table assignments."""
        
        # Generate placeholder contact info if needed
        has_email = 'email' in customer_data and customer_data['email']
        has_phone = 'phone' in customer_data and customer_data['phone']
        
        if not has_email and not has_phone and reservation_data['party_size'] < 6:
            # Create a deterministic hash based on name
            name = customer_data['name']
            name_hash = hashlib.md5(name.lower().strip().encode()).hexdigest()[:8]
            placeholder_email = f"guest-{name_hash}@restaurant.local"
            customer_data['email'] = placeholder_email
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Create or get existing customer
                customer_id = None
                if 'email' in customer_data and customer_data['email']:
                    # Check if customer exists
                    existing = await conn.fetchrow(
                        "SELECT id FROM customers WHERE email = $1", 
                        customer_data['email']
                    )
                    if existing:
                        customer_id = existing['id']
                
                if not customer_id and 'phone' in customer_data and customer_data['phone']:
                    # Check by phone
                    existing = await conn.fetchrow(
                        "SELECT id FROM customers WHERE phone = $1", 
                        customer_data['phone']
                    )
                    if existing:
                        customer_id = existing['id']
                
                # Create new customer if not found
                if not customer_id:
                    customer_record = await conn.fetchrow("""
                    INSERT INTO customers (name, email, phone, notes)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    customer_data['name'],
                    customer_data.get('email'),
                    customer_data.get('phone'),
                    customer_data.get('notes', '')
                    )
                    customer_id = customer_record['id']
                
                # 2. Create reservation
                reservation_record = await conn.fetchrow("""
                INSERT INTO reservations (
                    customer_id, party_size, reservation_date,
                    start_time, duration_minutes, notes, status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                customer_id,
                reservation_data['party_size'],
                reservation_data['reservation_date'],
                reservation_data['start_time'],
                reservation_data.get('duration_minutes', 90),
                reservation_data.get('notes', ''),
                reservation_data.get('status', 'pending')
                )
                
                reservation_id = reservation_record['id']
                
                # 3. Assign tables
                for table_id in table_ids:
                    await conn.execute("""
                    INSERT INTO table_assignments (reservation_id, table_id)
                    VALUES ($1, $2)
                    """, reservation_id, table_id)
                
                result = await self.get_reservation_by_id(reservation_id, conn)
                return result
    
    async def get_reservation_by_id(self, reservation_id: UUID, conn=Optional[Connection]) -> Dict[str, Any]:
        """Get a complete reservation with customer and table info."""
        # Use provided connection or acquire a new one
        use_provided_conn = conn is not None
        conn = conn or await self.pool.acquire()
        
        try:
            # Get reservation and customer info
            reservation = await conn.fetchrow("""
            SELECT 
                r.id, r.party_size, r.reservation_date, r.start_time,
                r.duration_minutes, r.notes, r.status, r.created_at,
                c.id as customer_id, c.name as customer_name, 
                c.email as customer_email, c.phone as customer_phone
            FROM 
                reservations r
            JOIN 
                customers c ON r.customer_id = c.id
            WHERE 
                r.id = $1
            """, reservation_id)
            
            if not reservation:
                return None
            
            # Get assigned tables
            tables = await conn.fetch("""
            SELECT 
                t.id, t.table_number, t.min_capacity, t.max_capacity, 
                t.is_shared, t.location
            FROM 
                tables t
            JOIN 
                table_assignments ta ON t.id = ta.table_id
            WHERE 
                ta.reservation_id = $1
            """, reservation_id)
            
            result = dict(reservation)
            result['tables'] = [dict(t) for t in tables]
            return result
        finally:
            # Only release the connection if we acquired it
            if not use_provided_conn and conn:
                await conn.close()
    
    async def get_reservations(
        self, 
        filters: Dict[str, Any] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get reservations with optional filtering."""
        query = """
        SELECT 
            r.id, r.party_size, r.reservation_date, r.start_time,
            r.duration_minutes, r.notes, r.status, r.created_at,
            c.id as customer_id, c.name as customer_name, 
            c.email as customer_email, c.phone as customer_phone
        FROM 
            reservations r
        JOIN 
            customers c ON r.customer_id = c.id
        """
        
        values = []
        conditions = []
        
        if filters:
            for i, (key, value) in enumerate(filters.items(), 1):
                if key == 'customer_id':
                    conditions.append(f"c.id = ${i}")
                    values.append(value)
                elif key == 'reservation_date':
                    conditions.append(f"r.reservation_date = ${i}")
                    values.append(value)
                elif key == 'status':
                    if isinstance(value, list):
                        placeholders = [f"${i + j}" for j in range(len(value))]
                        conditions.append(f"r.status IN ({', '.join(placeholders)})")
                        values.extend(value)
                    else:
                        conditions.append(f"r.status = ${i}")
                        values.append(value)
                elif key == 'table_id':
                    conditions.append(f"""
                    r.id IN (
                        SELECT reservation_id FROM table_assignments
                        WHERE table_id = ${i}
                    )
                    """)
                    values.append(value)
                elif key == 'date_from':
                    conditions.append(f"r.reservation_date >= ${i}")
                    values.append(value)
                elif key == 'date_to':
                    conditions.append(f"r.reservation_date <= ${i}")
                    values.append(value)
                    
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY r.reservation_date, r.start_time"
        query += f" LIMIT ${len(values) + 1} OFFSET ${len(values) + 2}"
        values.extend([limit, offset])
        
        async with self.pool.acquire() as conn:
            reservation_records = await conn.fetch(query, *values)
            
            if not reservation_records:
                return []
            
            # Get tables for all these reservations
            reservation_ids = [r['id'] for r in reservation_records]
            tables_query = """
            SELECT 
                ta.reservation_id,
                t.id, t.table_number, t.min_capacity, t.max_capacity, 
                t.is_shared, t.location
            FROM 
                table_assignments ta
            JOIN 
                tables t ON ta.table_id = t.id
            WHERE 
                ta.reservation_id = ANY($1)
            """
            
            tables_records = await conn.fetch(tables_query, reservation_ids)
            
            # Organize tables by reservation_id
            tables_by_reservation = {}
            for t in tables_records:
                res_id = t['reservation_id']
                if res_id not in tables_by_reservation:
                    tables_by_reservation[res_id] = []
                
                table_info = dict(t)
                del table_info['reservation_id']  # Remove extra field
                tables_by_reservation[res_id].append(table_info)
            
            # Combine reservations with their tables
            result = []
            for r in reservation_records:
                res_dict = dict(r)
                res_dict['tables'] = tables_by_reservation.get(r['id'], [])
                result.append(res_dict)
                
            return result
    
    async def update_reservation_status(
        self, 
        reservation_id: UUID, 
        status: str
    ) -> Dict[str, Any]:
        """Update a reservation's status."""
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow("""
            UPDATE reservations
            SET status = $1
            WHERE id = $2
            RETURNING id
            """, status, reservation_id)
            
            if record:
                return await self.get_reservation_by_id(reservation_id)
            return None
    
    async def update_reservation(
        self, 
        reservation_id: UUID,
        reservation_data: Dict[str, Any],
        table_ids: List[UUID] = None
    ) -> Dict[str, Any]:
        """Update a reservation with optional table reassignment."""
        fields = []
        values = []
        
        # Build dynamic update query
        for i, (key, value) in enumerate(reservation_data.items(), 1):
            if key in ['party_size', 'reservation_date', 'start_time', 
                       'duration_minutes', 'notes', 'status']:
                fields.append(f"{key} = ${i}")
                values.append(value)
        
        if not fields and table_ids is None:
            raise ValueError("No valid fields to update")
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Update reservation fields if any
                if fields:
                    query = f"""
                    UPDATE reservations 
                    SET {', '.join(fields)}
                    WHERE id = ${len(values) + 1}
                    RETURNING id
                    """
                    values.append(reservation_id)
                    
                    record = await conn.fetchrow(query, *values)
                    if not record:
                        return None
                
                # Update table assignments if provided
                if table_ids is not None:
                    # Delete current assignments
                    await conn.execute("""
                    DELETE FROM table_assignments
                    WHERE reservation_id = $1
                    """, reservation_id)
                    
                    # Create new assignments
                    for table_id in table_ids:
                        await conn.execute("""
                        INSERT INTO table_assignments (reservation_id, table_id)
                        VALUES ($1, $2)
                        """, reservation_id, table_id)
                
                # Return updated reservation
                return await self.get_reservation_by_id(reservation_id)
    
    async def delete_reservation(self, reservation_id: UUID) -> bool:
        """Delete a reservation and its table assignments."""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
            DELETE FROM reservations WHERE id = $1
            """, reservation_id)
            return result == "DELETE 1"

    # ===================
    # Restaurant Hours Operations
    # ===================
    
    async def get_hours(self) -> List[Dict[str, Any]]:
        """Get restaurant operating hours for all days."""
        async with self.pool.acquire() as conn:
            records = await conn.fetch("""
            SELECT 
                id, day_of_week, open_time, close_time, 
                last_reservation_time
            FROM restaurant_hours
            ORDER BY day_of_week
            """)
            return [dict(r) for r in records]
    
    async def get_special_hours(self, date_from: date = None, date_to: date = None) -> List[Dict[str, Any]]:
        """Get special hours for holidays and events with optional date range filtering."""
        query = """
        SELECT id, date, open_time, close_time, last_reservation_time, 
            is_closed, name, description, created_at, updated_at
        FROM special_hours
        """
        
        values = []
        conditions = []
        
        if date_from:
            conditions.append(f"date >= ${len(values) + 1}")
            values.append(date_from)
        
        if date_to:
            conditions.append(f"date <= ${len(values) + 1}")
            values.append(date_to)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY date"
        
        async with self.pool.acquire() as conn:
            records = await conn.fetch(query, *values)
            return [dict(r) for r in records]

    async def get_special_hours_by_date(self, date_val: date) -> Dict[str, Any]:
        """Get special hours for a specific date."""
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow("""
            SELECT id, date, open_time, close_time, last_reservation_time, 
                is_closed, name, description, created_at, updated_at
            FROM special_hours
            WHERE date = $1
            """, date_val)
            return dict(record) if record else None

    async def set_special_hours(
        self, 
        date_val: date,
        name: str,
        description: str = None,
        is_closed: bool = False,
        open_time: time = None,
        close_time: time = None,
        last_reservation_time: time = None
    ) -> Dict[str, Any]:
        """Set special hours for a specific date (holiday or event)."""
        async with self.pool.acquire() as conn:
            # Ensure times are provided if not closed
            if not is_closed:
                if not open_time or not close_time or not last_reservation_time:
                    raise ValueError("Open time, close time, and last reservation time must be provided if not closed")
                
                # Validate times
                if close_time <= open_time:
                    raise ValueError("Close time must be after open time")
                
                if last_reservation_time <= open_time or last_reservation_time >= close_time:
                    raise ValueError("Last reservation time must be between open and close time")
            
            record = await conn.fetchrow("""
            INSERT INTO special_hours (
                date, name, description, is_closed, 
                open_time, close_time, last_reservation_time
            ) 
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (date) 
            DO UPDATE SET 
                name = $2,
                description = $3,
                is_closed = $4,
                open_time = $5,
                close_time = $6,
                last_reservation_time = $7
            RETURNING 
                id, date, open_time, close_time, last_reservation_time, 
                is_closed, name, description, created_at, updated_at
            """, 
            date_val, name, description, is_closed, 
            open_time, close_time, last_reservation_time
            )
            return dict(record)

    async def delete_special_hours(self, special_hours_id: UUID) -> bool:
        """Delete special hours."""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
            DELETE FROM special_hours WHERE id = $1
            """, special_hours_id)
            return result == "DELETE 1"

    async def set_hours(
        self, 
        day_of_week: int,
        open_time: time,
        close_time: time,
        last_reservation_time: time
    ) -> Dict[str, Any]:
        """Set or update restaurant hours for a specific day."""
        async with self.pool.acquire() as conn:
            record = await conn.fetchrow("""
            INSERT INTO restaurant_hours (
                day_of_week, open_time, close_time, last_reservation_time
            ) 
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (day_of_week) 
            DO UPDATE SET 
                open_time = $2, 
                close_time = $3, 
                last_reservation_time = $4
            RETURNING 
                id, day_of_week, open_time, close_time, last_reservation_time
            """, 
            day_of_week, open_time, close_time, last_reservation_time
            )
            return dict(record)
    
    # Modify this existing method to check special hours
    async def is_valid_reservation_time(
        self,
        reservation_date: date,
        start_time: time,
        duration_minutes: int = 90
    ) -> bool:
        """Check if a reservation time is valid based on restaurant hours."""
        # Calculate end time
        end_time_dt = datetime.combine(datetime.min, start_time) + timedelta(minutes=duration_minutes)
        end_time = end_time_dt.time()
        
        async with self.pool.acquire() as conn:
            # First check if this is a special day (holiday, event, etc.)
            special_day = await conn.fetchrow("""
            SELECT is_closed, open_time, close_time, last_reservation_time
            FROM special_hours
            WHERE date = $1
            """, reservation_date)
            
            if special_day:
                # If restaurant is closed on this special day
                if special_day['is_closed']:
                    return False
                    
                # Use special day hours
                if start_time < special_day['open_time']:
                    return False
                    
                if start_time > special_day['last_reservation_time']:
                    return False
                    
                if end_time > special_day['close_time']:
                    return False
                    
                return True
            
            # If not a special day, continue with regular hour check
            day_of_week = reservation_date.weekday()
            
            hours = await conn.fetchrow("""
            SELECT open_time, close_time, last_reservation_time
            FROM restaurant_hours
            WHERE day_of_week = $1
            """, day_of_week)
            
            if not hours:
                # No hours set for this day (restaurant closed)
                return False
                
            # Check against regular hours
            if start_time < hours['open_time']:
                return False
                
            if start_time > hours['last_reservation_time']:
                return False
                
            if end_time > hours['close_time']:
                return False
                
            return True

def generate_placeholder_contact(name: str, party_size: int) -> dict:
    """Generate placeholder contact info for small parties without provided contact details."""
    if party_size < 6:
        # Create a deterministic hash based on name
        name_hash = hashlib.md5(name.lower().strip().encode()).hexdigest()[:8]
        placeholder_email = f"guest-{name_hash}@restaurant.local"
        return {
            "email": placeholder_email,
            "phone": None
        }
    else:
        # For larger parties, don't provide a fallback
        return {
            "email": None,
            "phone": None
        }
    
db = Database()