"""
Standalone script for daily assignment.
Can be run as:
  - Cloud Run Job (triggered by Cloud Scheduler)
  - Direct CLI: python cron_assignment.py
"""

import json
import sys

# Need to set up streamlit secrets for BQ access when running standalone
import os
if not os.environ.get("STREAMLIT_SERVER_PORT"):
    # Running outside Streamlit — set up minimal config
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

import streamlit as st

from assignment import run_assignment


def main():
    result = run_assignment()
    print(json.dumps(result, indent=2, default=str))

    if "error" in result:
        sys.exit(1)

    print(f"\nAssignment complete: {result.get('assigned', 0)} reviews assigned")
    return result


if __name__ == "__main__":
    main()
