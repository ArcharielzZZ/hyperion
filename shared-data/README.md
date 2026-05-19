# Shared data (gitignored)

Unpack team cloud bundles here, or symlink to a shared drive.

See [docs/DATA_ARTIFACTS.md](../docs/DATA_ARTIFACTS.md) for restore steps and expected layout.

Suggested layout after unpack:

```text
shared-data/
├── postgres/hyperion.dump
└── parquet/fills/year=…/…
```

Set in `.env` if needed:

```env
PIPELINE_DATA_DIR=shared-data/parquet
```
