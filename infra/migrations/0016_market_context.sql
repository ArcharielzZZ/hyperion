-- Migration 0016: Market Context (Funding Rates & Macro Events)
-- This migration adds tables to store historical funding rates and macroeconomic events
-- for use in behavioral analysis and event studies.

-- Table for Historical Funding Rates (e.g., from Hyperliquid Info API)
CREATE TABLE IF NOT EXISTS historical_funding (
    asset VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    funding_rate NUMERIC NOT NULL,
    premium NUMERIC, -- Optional: Premium Index
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (asset, timestamp)
);

-- Table for Macroeconomic Events (e.g., from FRED, CPI, FOMC)
CREATE TABLE IF NOT EXISTS macro_events (
    event_id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL, -- e.g., 'FOMC', 'CPI'
    timestamp TIMESTAMPTZ NOT NULL,
    actual_value NUMERIC,
    forecast_value NUMERIC,
    impact_score NUMERIC, -- Optional: How strong was the deviation?
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indices for fast time-based queries
CREATE INDEX IF NOT EXISTS idx_historical_funding_time ON historical_funding(timestamp);
CREATE INDEX IF NOT EXISTS idx_macro_events_time ON macro_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_macro_events_type ON macro_events(event_type);
