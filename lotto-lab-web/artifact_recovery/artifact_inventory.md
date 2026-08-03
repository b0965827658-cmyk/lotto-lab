# V3.0.0 Artifact Recovery Inventory

## Search scope

- Project and old-project locations under `D:\Lotto`
- `C:\Users` including Desktop, Documents, Downloads, and `C:\Users\Owner\OneDrive`
- Other fixed-disk locations on `C:\`, `D:\`, and `E:\`
- Hidden files and files normally excluded by ignore rules
- Complete Git history for the current repository

Search names and patterns:

- `models_v2`
- `manifest.json`
- `weights_v2_tw539.json`
- `weights_v2_ca_fantasy5.json`
- `*.joblib`
- `*.pkl`

## 已找到

No V3.0.0 model artifacts were found.

No file can be listed with a V3.0.0-compatible path, size, SHA-256, and modification time because no matching Lotto/TW539/Fantasy5 artifact exists on the searched disks.

Unrelated `manifest.json` and `.pkl` files were found in browser installations, Python/joblib/numpy test data, application caches, and other installed software. They have no Lotto, TW539, Fantasy5, or `models_v2` path relationship and are not recoverable V3.0.0 artifacts.

## 找不到

| Required artifact | Expected location | Ever tracked by current Git history | Excluded by current `.gitignore` | Likely recovery source |
|---|---|---|---|---|
| TW539 V2 model directory and model binaries | `data/models_v2/tw539/` | No | No | Original pre-migration computer or an external backup |
| TW539 artifact manifest | `data/models_v2/tw539/manifest.json` | No | No | Original pre-migration computer or an external backup |
| TW539 V2 weights | `data/weights_v2_tw539.json` | No | No | Original pre-migration computer or an external backup |
| Fantasy5 V2 model directory and model binaries | `data/models_v2/ca-fantasy5/` | No | No | Original pre-migration computer or an external backup |
| Fantasy5 artifact manifest | `data/models_v2/ca-fantasy5/manifest.json` | No | No | Original pre-migration computer or an external backup |
| Fantasy5 V2 weights | `data/weights_v2_ca_fantasy5.json` | No | No | Original pre-migration computer or an external backup |
| V3.0.0 baseline manifest | Expected original V3.0.0 artifact/baseline location; no path is declared in the current repository | No | No | Original pre-migration computer or an external backup |

## Git and ignore findings

The complete current Git history contains no paths matching:

- `data/models_v2/`
- `data/weights_v2*`
- `*.joblib`
- `*.pkl`
- a V3.0.0 model/baseline `manifest.json`

Current `.gitignore` rules are:

```text
__pycache__/
*.pyc
.DS_Store
outputs/
work/
.env
```

There is no current rule for `*.joblib`, `*.pkl`, or `models_v2/`. Direct `git check-ignore --no-index` checks confirm that the expected artifact paths are not ignored.

Possible historical explanation: artifacts could have been stored outside this repository, inside an ignored `outputs/` or `work/` directory on the old computer, or never added to Git. No such artifact is present in the searched copies on this computer.

## Recovery conclusion

- V3.0.0 supervised model artifacts found: **No**
- `weights_v2` files found: **No**
- V3.0.0 model/baseline manifest found: **No**
- Direct recovery from this computer or current Git repository: **Not possible**
- Required recovery source: the original pre-migration computer or a verified external backup

No model was trained, created, downloaded, or regenerated during this search.
