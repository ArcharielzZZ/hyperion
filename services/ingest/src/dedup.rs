use crate::normalizer::NormalizedBatch;
use std::collections::{HashSet, VecDeque};

#[derive(Debug)]
pub struct DedupCache {
    capacity: usize,
    order: VecDeque<String>,
    seen: HashSet<String>,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct DedupStats {
    pub duplicates: u64,
}

impl DedupCache {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            order: VecDeque::with_capacity(capacity.max(1)),
            seen: HashSet::with_capacity(capacity.max(1)),
        }
    }

    pub fn filter_batch(&mut self, mut batch: NormalizedBatch) -> (NormalizedBatch, DedupStats) {
        let mut duplicates = 0;

        batch.fills = self.retain_unique(batch.fills, |record| &record.event_key, &mut duplicates);
        batch.positions = self.retain_unique(
            batch.positions,
            |record| &record.snapshot_key,
            &mut duplicates,
        );
        batch.pnl_snapshots = self.retain_unique(
            batch.pnl_snapshots,
            |record| &record.snapshot_key,
            &mut duplicates,
        );
        batch.market_snapshots = self.retain_unique(
            batch.market_snapshots,
            |record| &record.snapshot_key,
            &mut duplicates,
        );
        batch.trade_ticks = self.retain_unique(
            batch.trade_ticks,
            |record| &record.event_key,
            &mut duplicates,
        );
        batch.liquidations = self.retain_unique(
            batch.liquidations,
            |record| &record.event_key,
            &mut duplicates,
        );

        (batch, DedupStats { duplicates })
    }

    fn retain_unique<T, F>(&mut self, records: Vec<T>, key: F, duplicates: &mut u64) -> Vec<T>
    where
        F: Fn(&T) -> &String,
    {
        let mut unique = Vec::with_capacity(records.len());
        for record in records {
            let dedup_key = key(&record).clone();
            if self.seen.insert(dedup_key.clone()) {
                self.order.push_back(dedup_key);
                self.prune();
                unique.push(record);
            } else {
                *duplicates += 1;
            }
        }
        unique
    }

    fn prune(&mut self) {
        while self.order.len() > self.capacity {
            if let Some(key) = self.order.pop_front() {
                self.seen.remove(&key);
            }
        }
    }
}

impl Default for DedupCache {
    fn default() -> Self {
        Self::new(100_000)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::normalizer::FillRecord;
    use chrono::Utc;

    #[test]
    fn filters_duplicate_fill_event_keys() {
        let now = Utc::now();
        let mut cache = DedupCache::new(16);
        let batch = NormalizedBatch {
            fills: vec![
                FillRecord {
                    wallet: "0xabc".to_string(),
                    coin: "BTC".to_string(),
                    side: "buy".to_string(),
                    size: 1.0,
                    leverage: 0.0,
                    price: 1.0,
                    timestamp: now,
                    event_key: "fill-1".to_string(),
                    fill_dir: None,
                    closed_pnl_usd: None,
                    fee_usd: None,
                },
                FillRecord {
                    wallet: "0xabc".to_string(),
                    coin: "BTC".to_string(),
                    side: "buy".to_string(),
                    size: 1.0,
                    leverage: 0.0,
                    price: 1.0,
                    timestamp: now,
                    event_key: "fill-1".to_string(),
                    fill_dir: None,
                    closed_pnl_usd: None,
                    fee_usd: None,
                },
            ],
            ..Default::default()
        };

        let (filtered, stats) = cache.filter_batch(batch);
        assert_eq!(filtered.fills.len(), 1);
        assert_eq!(stats.duplicates, 1);
    }
}
