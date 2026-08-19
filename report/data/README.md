# Report data contract

`report/data/<name>/` is the boundary between benchmark artifacts and report
figures. Dummy and measured results use the same two files:

```text
<name>/
├── manifest.json
└── metrics.csv
```

`manifest.json` records `schema_version`, `kind` (`dummy` or `measured`), row
count and source provenance. `metrics.csv` is long-form data with one numeric
metric per row.

| Column | Meaning |
| --- | --- |
| `graph` | `throughput`, `speedup`, `latency`, `resource`, `nodewise` or `scaling` |
| `workload`, `backend` | Report display labels |
| `speedup`, `repeat` | Replay case coordinates |
| `node_count` | Scaling case storage-node count; empty otherwise |
| `node` | Profile node or `aggregate`; empty for non-resource metrics |
| `elapsed_seconds` | Throughput interval endpoint; empty for scalar metrics |
| `metric`, `value`, `unit` | Stable metric name, numeric value and validated unit |

Metric names and units are defined once in
[`report_data.py`](../report_data.py). Do not add figure-specific CSV formats.
Add a metric there, teach the artifact importer how to populate it, and then
consume it in the renderer.

## Versioned dummy data

`dummy/metrics.csv` is the editable placeholder dataset. It is generated
deterministically, so layout changes can be reviewed without measured results.

```bash
python -m report.generate_dummy_data
python -m report.plot_results
```

Changing only the `value` cells is enough when the existing workload/backend
matrix is unchanged. Run the renderer afterward; it validates the schema and
refuses non-finite values, unknown metrics and incorrect units.

## Measured data

The report experiment runner already records every case and its retrieved
artifact path in `matrix-results.jsonl`. Normalize those artifacts with:

```bash
python -m report.import_artifacts \
  --state-root outputs/report-experiments-staged \
  --network-link-gbps xfs=100 \
  --network-link-gbps 3FS=100 \
  --network-link-gbps pNFS=100

python -m report.plot_results \
  --data-dir report/data/measured
```

The importer reads `l2_replay_stats.json`, `l2_io_interval.tsv` and
`profile/<node>/{disk,network}.tsv`. Network utilization needs the directional
link capacity supplied by `--network-link-gbps`; without it, the importer leaves
that metric out and records a warning in the measured manifest.

Distributed backend aggregate는 `storage` role, `xfs` aggregate는 `replay`
role을 우선 사용합니다. Importer의 aggregate는 선택된 node p95의 equal-weight
mean입니다. Device/link capacity가 서로 다른 cluster에서는 capacity-weighted 값을
계산해 `metrics.csv`의 `aggregate` row를 교체하고 provenance에 방법을 기록합니다.

By default measured data and figures are generated under ignored
`report/data/measured/` and `report/figures/measured/`. After checking coverage,
provenance and layout, render explicitly to `report/figures/` when the report is
ready to replace its placeholders:

```bash
python -m report.plot_results \
  --data-dir report/data/measured \
  --output-dir report/figures
```

Use `--figure throughput`, `--figure speedup`, and so on to regenerate only one
figure family. Missing combinations are rendered as `N/A` rather than silently
substituting another metric.
