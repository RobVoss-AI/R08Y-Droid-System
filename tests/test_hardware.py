"""Compatibility entry point for the R08Y Pi smoke test.

Preferred command:
    python -m r08y --config config.yaml --smoke-test

Optional physical exercise flags are forwarded unchanged. Motor movement still
requires both --exercise-mobility and --confirm-wheels-raised.
"""

import sys
from pathlib import Path


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parents[1]))
    if "--smoke-test" not in sys.argv:
        sys.argv.append("--smoke-test")
    from r08y.__main__ import main

    main()
