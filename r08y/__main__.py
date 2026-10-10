"""Entry point for `python -m r08y`."""

import argparse
import asyncio
import os
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
        "--audio-test",
        action="store_true",
        help="Run a guided XVF3800 microphone and speaker test only",
    )
    parser.add_argument(
        "--speech-test",
        action="store_true",
        help="Speak a short TTS audition without starting other hardware",
    )
    parser.add_argument(
        "--expression-test",
        action="store_true",
        help="Run a guided pan/tilt expression test; wheels remain inactive",
    )
    parser.add_argument(
        "--wake-word-test",
        action="store_true",
        help="Listen for the configured custom wake word without starting other hardware",
    )
    parser.add_argument(
        "--speech-voice",
        help="Temporarily override the voice for --speech-test",
    )
    parser.add_argument(
        "--speech-rate",
        help="Temporarily override the rate for --speech-test, such as +1%%",
    )
    parser.add_argument(
        "--speech-pitch",
        help="Temporarily override the pitch for --speech-test, such as +2Hz",
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
    speech_overrides = {
        "voice": args.speech_voice,
        "rate": args.speech_rate,
        "pitch": args.speech_pitch,
    }
    if any(speech_overrides.values()) and not args.speech_test:
        parser.error("speech overrides require --speech-test")
    for field, value in speech_overrides.items():
        if value is not None:
            setattr(config.tts, field, value)
    errors = validate_config(config)
    if args.check_config:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        print("Configuration is valid.")
        return

    selected_test_modes = sum(
        bool(selected)
        for selected in (
            args.audio_test,
            args.speech_test,
            args.expression_test,
            args.wake_word_test,
            args.smoke_test,
        )
    )
    if selected_test_modes > 1:
        parser.error(
            "--audio-test, --speech-test, --wake-word-test, --expression-test, "
            "and --smoke-test are separate test modes"
        )
    if (
        args.audio_test
        or args.speech_test
        or args.expression_test
        or args.wake_word_test
    ) and (
        args.exercise_hardware or args.exercise_mobility
    ):
        parser.error("hardware exercise flags cannot be combined with guided test modes")

    if args.audio_test:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        from r08y.diagnostics import print_smoke_results, run_audio_test

        try:
            results = asyncio.run(run_audio_test(config))
        except KeyboardInterrupt:
            print("\nR08Y audio test stopped.", flush=True)
            os._exit(130)
        print_smoke_results(results, heading="R08Y guided audio test")
        sys.exit(1 if any(result.status == "fail" for result in results) else 0)

    if args.speech_test:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        from r08y.diagnostics import print_smoke_results, run_speech_test

        try:
            results = asyncio.run(run_speech_test(config))
        except KeyboardInterrupt:
            print("\nR08Y speech test stopped.", flush=True)
            os._exit(130)
        print_smoke_results(results, heading="R08Y speech audition")
        sys.exit(1 if any(result.status == "fail" for result in results) else 0)

    if args.wake_word_test:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        from r08y.diagnostics import print_smoke_results, run_wake_word_test

        try:
            results = asyncio.run(run_wake_word_test(config))
        except KeyboardInterrupt:
            print("\nR08Y wake-word test stopped.", flush=True)
            os._exit(130)
        print_smoke_results(results, heading="R08Y ROBY wake-word test")
        sys.exit(1 if any(result.status == "fail" for result in results) else 0)

    if args.expression_test:
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        from r08y.diagnostics import print_smoke_results, run_expression_test

        try:
            results = asyncio.run(run_expression_test(config))
        except KeyboardInterrupt:
            print("\nR08Y expression test stopped. Returning pan/tilt home.", flush=True)
            os._exit(130)
        print_smoke_results(results, heading="R08Y guided expression test")
        sys.exit(1 if any(result.status == "fail" for result in results) else 0)

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
            print("\nR08Y hardware smoke test stopped.", flush=True)
            # A native camera call can remain blocked in a worker thread. The OS
            # releases its devices when this diagnostic process exits.
            os._exit(130)
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
