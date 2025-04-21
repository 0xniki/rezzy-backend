CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE reservation_status AS ENUM ('pending', 'confirmed', 'seated', 'completed', 'cancelled', 'no_show');

CREATE TABLE tables (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    table_number VARCHAR(10) NOT NULL UNIQUE,
    min_capacity INTEGER NOT NULL CHECK (min_capacity > 0),
    max_capacity INTEGER NOT NULL CHECK (max_capacity >= min_capacity),
    is_shared BOOLEAN NOT NULL DEFAULT FALSE,
    location VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE chairs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), 
    table_id UUID NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    is_assigned BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE restaurant_hours (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    open_time TIME NOT NULL,
    close_time TIME NOT NULL CHECK (close_time > open_time),
    last_reservation_time TIME NOT NULL CHECK (last_reservation_time < close_time),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (day_of_week)
);

CREATE TABLE special_hours (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date DATE NOT NULL,
    open_time TIME,
    close_time TIME,
    last_reservation_time TIME,
    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (date)
);

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100),
    phone VARCHAR(20),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CHECK (email IS NOT NULL OR phone IS NOT NULL)
);

CREATE TABLE reservations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL REFERENCES customers(id),
    party_size INTEGER NOT NULL CHECK (party_size > 0),
    reservation_date DATE NOT NULL,
    start_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 90,
    notes TEXT,
    status reservation_status NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE table_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reservation_id UUID NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
    table_id UUID NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (reservation_id, table_id)
);

CREATE INDEX idx_reservations_date_time ON reservations(reservation_date, start_time);
CREATE INDEX idx_table_assignments_reservation ON table_assignments(reservation_id);
CREATE INDEX idx_table_assignments_table ON table_assignments(table_id);
CREATE INDEX idx_chairs_table ON chairs(table_id);

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_tables_timestamp BEFORE UPDATE ON tables
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    
CREATE TRIGGER update_chairs_timestamp BEFORE UPDATE ON chairs
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    
CREATE TRIGGER update_restaurant_hours_timestamp BEFORE UPDATE ON restaurant_hours
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
CREATE TRIGGER update_special_hours_timestamp BEFORE UPDATE ON special_hours
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
CREATE TRIGGER update_customers_timestamp BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
    
CREATE TRIGGER update_reservations_timestamp BEFORE UPDATE ON reservations
    FOR EACH ROW EXECUTE PROCEDURE update_timestamp();
