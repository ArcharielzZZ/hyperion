-- sql/migrations/001_wallet_multi_window_stats.sql

-- Basic wallet information for all 2 million+ wallets
CREATE TABLE IF NOT EXISTS wallets (
    address VARCHAR(42) PRIMARY KEY,
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    total_pnl_all_time DECIMAL DEFAULT 0,
    total_trades_all_time INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'PENDING', -- 'PENDING', 'PROMOTED', 'BANNED'
    label VARCHAR(30) DEFAULT 'NEW_WALLET', -- 'NEW_WALLET', 'NORMAL', 'HIGH_FREQUENCY', 'ULTRA_HF_BOT'
    is_promoted BOOLEAN DEFAULT FALSE -- Beibehalten für Abwärtskompatibilität, kann später durch status='PROMOTED' ersetzt werden
);

-- Daily aggregated statistics for each wallet (The "Daily Summary")
-- This table helps to calculate multi-window stats efficiently.
CREATE TABLE IF NOT EXISTS wallet_daily_stats (
    address VARCHAR(42) REFERENCES wallets(address),
    date DATE,
    daily_pnl DECIMAL DEFAULT 0,
    daily_trades INTEGER DEFAULT 0,
    daily_volume DECIMAL DEFAULT 0,
    PRIMARY KEY (address, date)
);

-- Multi-window performance statistics (7d, 1m, 3m, 6m)
CREATE TABLE IF NOT EXISTS wallet_performance (
    address VARCHAR(42) PRIMARY KEY REFERENCES wallets(address),
    
    -- 7 Days
    pnl_7d DECIMAL DEFAULT 0,
    roi_7d DECIMAL DEFAULT 0,
    trades_7d INTEGER DEFAULT 0,
    
    -- 1 Month (30 Days)
    pnl_1m DECIMAL DEFAULT 0,
    roi_1m DECIMAL DEFAULT 0,
    trades_1m INTEGER DEFAULT 0,
    
    -- 3 Months (90 Days)
    pnl_3m DECIMAL DEFAULT 0,
    roi_3m DECIMAL DEFAULT 0,
    trades_3m INTEGER DEFAULT 0,
    
    -- 6 Months (180 Days)
    pnl_6m DECIMAL DEFAULT 0,
    roi_6m DECIMAL DEFAULT 0,
    trades_6m INTEGER DEFAULT 0,
    
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for faster aggregation
CREATE INDEX IF NOT EXISTS idx_wallet_daily_stats_date ON wallet_daily_stats(date);
CREATE INDEX IF NOT EXISTS idx_wallets_last_seen ON wallets(last_seen);
