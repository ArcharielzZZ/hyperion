ALTER TABLE traders SET (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold = 500,
    autovacuum_analyze_scale_factor = 0.005,
    autovacuum_analyze_threshold = 250
);

ALTER TABLE trader_discovery_rankings SET (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold = 250,
    autovacuum_analyze_scale_factor = 0.005,
    autovacuum_analyze_threshold = 100
);
