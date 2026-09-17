from __future__ import annotations

import os
import sys

from edt_readonly_mcp.server import main


if __name__ == "__main__":
    if not os.environ.get("EDT_PROJECT_PATH"):
        print("EDT_PROJECT_PATH is required for the stdio server", file=sys.stderr)
        raise SystemExit(2)
    main()
