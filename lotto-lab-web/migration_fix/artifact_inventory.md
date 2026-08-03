# V3.0.0 Artifact Inventory

Inventory scope: `D:\Lotto\lotto-lab\lotto-lab-web`

This inventory was produced after stopping the local `server.py` process. No model training, data generation, or artifact repair was performed.

## 已存在

| Path | Size | SHA-256 | Modified (UTC) | Git tracked | Classification |
|---|---:|---|---|---|---|
| `data/tw539_model_store.json` | 2,996 bytes | `3A41218FD7E0E075AF976E7913CBDC2E13921AD69EA83E3A3F65435443061E21` | `2026-08-02T15:53:37.9414010Z` | Yes | Formal-engine JSON model state; not a V2 supervised model artifact or V2 manifest |

## 缺失

MISSING_ARTIFACT

| Game | Required path | Expected type | Recovery source |
|---|---|---|---|
| TW539 | `data/models_v2/tw539/` | Model artifact directory | Restore from the pre-migration/original V3.0.0 environment or its verified backup |
| TW539 | `data/models_v2/tw539/manifest.json` | Artifact manifest | Restore from the pre-migration/original V3.0.0 environment or its verified backup |
| TW539 | `data/weights_v2_tw539.json` | V2 ensemble weights | Restore from the pre-migration/original V3.0.0 environment or its verified backup |
| Fantasy5 | `data/models_v2/ca-fantasy5/` | Model artifact directory | Restore from the pre-migration/original V3.0.0 environment or its verified backup |
| Fantasy5 | `data/models_v2/ca-fantasy5/manifest.json` | Artifact manifest | Restore from the pre-migration/original V3.0.0 environment or its verified backup |
| Fantasy5 | `data/weights_v2_ca_fantasy5.json` | V2 ensemble weights | Restore from the pre-migration/original V3.0.0 environment or its verified backup |

No V3.0.0 baseline manifest was found.

## 疑似舊版本或不同 artifact family

- `data/tw539_model_store.json` belongs to the formal walk-forward JSON state path used by `server.py`. It does not satisfy `analysis_v2.py` paths under `data/models_v2/tw539/` and does not replace `data/weights_v2_tw539.json`.
- `public/manifest.webmanifest` is the PWA application manifest, not a model or V3.0.0 baseline manifest.
- No Fantasy5 `model_store`, walk-forward model artifact, or V2 weight file was found.

## Search evidence

- Current filesystem search covered the project, `D:\Lotto`, and available common user backup folders.
- Git history contains `data/tw539_model_store.json`, but no `data/models_v2/` or `data/weights_v2*` paths.
- Therefore the missing artifacts cannot be recovered from the current Git repository or the searched local migration copy. They must come from the original pre-migration machine/environment or a verified external backup.
