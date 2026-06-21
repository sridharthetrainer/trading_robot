#!/usr/bin/env python3
"""Print current learning/probation/live universe tiers."""

from __future__ import annotations

import json
from universe_manager import describe_universe


def main() -> int:
    print(json.dumps(describe_universe(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
