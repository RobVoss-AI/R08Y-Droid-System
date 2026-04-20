"""Entry point for `python -m r08y`."""

import argparse
import asyncio
import sys

from r08y.main import run


def main():
    parser = argparse.ArgumentParser(description="R08Y Droid System")
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(config_path=args.config))
    except KeyboardInterrupt:
        print("\nR08Y shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
