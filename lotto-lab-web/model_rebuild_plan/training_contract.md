# Training Contract

## Immutable input snapshots

| Game | Canonical file | Rows | First date | Last date / cutoff | File SHA-256 | Canonical row SHA-256 |
|---|---|---:|---|---|---|---|
| TW539 | `data/tw539_database.json` | 1000 | 2023-04-28 | 2026-06-30 | `8485a7813601aa394c4c653652e502cee46bb168404f1d673ffe76a9531d74c2` | `ad2aea59e3d57e0c5e95600ede777f61e964949ad6e8021b0b4b3ed9107d5dde` |
| Fantasy5 | `data/ca_fantasy5_database_v2.json` | 365 | 2025-08-01 | 2026-07-31 | `dd708d1a65b5a597f4ef8d3d73a57a389f95cf288f13b9572b1b7298e47997be` | `74b78bad133b9d25fa2cfe448fa619bda76e2f9b71ef8e3e8d43d570adf49dc6` |

Do not merge live API, Pilio, sc888, newer draws, Prediction, or Battle data. Abort if row count, cutoff, file hash, canonical hash, validation, or sort order differs.

Code commit: `89de75e56ba8cfd1445990f6f6de1682aba5e976`

## Feature schemas

TW539 feature order:

`freq_5, freq_10, freq_30, freq_100, freq_300, gap, repeat_prev, repeat_2, neighbor, tail, odd, size, zone, sum, span, ac, same_tail, consecutive, weekday, month`

Schema SHA-256: `c93609434d12c57fb3486dfbe2cca1a184af398f77c2b04441b172bbf31bbeb5`

Fantasy5 feature order:

`freq_5, freq_7, freq_14, freq_30, freq_100, freq_300, gap, repeat_prev, repeat_2, pair, tail, odd, size, zone, sum, span, ac, same_tail, consecutive, weekday_pacific, dst_pacific, month_pacific`

Schema SHA-256: `93b999532f79a01ad80a5d581b3967bfd1e7e784826845a8bc37adb84059e889`

Feature schema version: `analysis-v2-2026.08-independent-baselines-v2@89de75e`

## Training behavior

- Sort by `(date, string(period))` ascending.
- Build prequential features using only rows before each target.
- Final artifact training window: last 300 eligible historical rows, with the implementation's `start=max(1,target-300)` behavior.
- Maximum number: 39; pick count: 5.
- Seed: `20260801`.
- Train each game independently.
- Build in an isolated staging directory. Production paths remain untouched until validation and an explicit promotion step.
- A rebuild attempt must write only inside its versioned staging directory until accepted.

## Manifest minimum fields

Every manifest must include release identity, game, model version, seed, trained timestamp, training cutoff, row count, file and canonical data hashes, feature names/order, feature schema version/hash, code commit, Python/package versions, thread controls, model hyperparameters, artifact paths and hashes, weight-file hash, walk-forward summary, and an explicit statement that this is a rebuild rather than a recovered V3.0.0 copy.

