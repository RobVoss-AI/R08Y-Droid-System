"""Entry point for `python -m r08y`."""

import argparse
import asyncio
import sys

from r08y.config import load_config, validate_config


def main():
    parser = argparse.ArgumentParser(description="R08Y Droid System")
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit without touching hardware",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run passive Pi hardware discovery and exit",
    )
    parser.add_argument(
        "--exercise-hardware",
        action="store_true",
        help="Open camera/audio/sensors and home pan/tilt during smoke test",
    )
    parser.add_argument(
        "--exercise-mobility",
        action="store_true",
        help="Move each wheel pattern at low speed; requires --confirm-wheels-raised",
    )
    parser.add_argument(
        "--confirm-wheels-raised",
        action="store_true",
        help="Confirm the robot is supported with every wheel clear",
    )
    parser.add_argument("--json", action="store_true", help="Print smoke-test results as JSON")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    errors = validate_config(config)
    if args.check_config:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        print("Configuration is valid.")
        return

    if args.exercise_mobility and not args.confirm_wheels_raised:
        parser.error("--exercise-mobility requires --confirm-wheels-raised")
    if (args.exercise_hardware or args.exercise_mobility) and not args.smoke_test:
        parser.error("hardware exercise flags require --smoke-test")

    if args.smoke_test:
        from r08y.diagnostics import print_smoke_results, run_smoke_test

        try:
            results = asyncio.run(
                run_smoke_test(
                    config,
                    exercise=args.exercise_hardware or args.exercise_mobility,
                    exercise_mobility=args.exercise_mobility,
                )
            )
        except KeyboardInterrupt:
            print("\nR08Y hardware smoke test stopped.")
            sys.exit(130)
        print_smoke_results(results, as_json=args.json)
        sys.exit(1 if any(result.status == "fail" for result in results) else 0)

    try:
        from r08y.main import run

        asyncio.run(run(config_path=args.config))
    except KeyboardInterrupt:
        print("\nR08Y shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
