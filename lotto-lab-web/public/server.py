"""Compatibility launcher for historical `python public/server.py` shortcuts.

Production has exactly one server implementation. Keeping analysis code in this
public directory previously made it possible to start a legacy process that did
not share the canonical analysis queue.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    canonical_root = str(Path(__file__).resolve().parents[1])
    if canonical_root in sys.path:
        sys.path.remove(canonical_root)
    sys.path.insert(0, canonical_root)

    import server as production_server

    if Path(production_server.__file__).resolve() == Path(__file__).resolve():
        raise RuntimeError("canonical Production server could not be resolved")
    production_server.main()


if __name__ == "__main__":
    main()
