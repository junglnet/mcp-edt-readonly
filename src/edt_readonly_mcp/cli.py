from __future__ import annotations

import argparse
import os
import sys

from edt_readonly_mcp.server import main as server_main


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Read-only MCP server for 1C:EDT projects")
    parser.add_argument("--project", default=os.environ.get("EDT_PROJECT_PATH"), help="Path to the 1C:EDT project directory")
    args = parser.parse_args()
    if not args.project:
        print("Project path is required. Pass --project or set EDT_PROJECT_PATH.", file=sys.stderr)
        return 2
    os.environ["EDT_PROJECT_PATH"] = args.project
    server_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
