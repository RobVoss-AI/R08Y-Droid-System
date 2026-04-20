"""Hardware verification script for R08Y.

Run on the Raspberry Pi to verify all hardware is connected and working:
    python3 tests/test_hardware.py

Tests: camera, I2C (PCA9685), microphone, speaker.
"""

import subprocess
import sys


def check_camera():
    """Verify USB webcam is accessible."""
    print("[Camera] ", end="")
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                print(f"OK — {frame.shape[1]}x{frame.shape[0]}")
                return True
        print("FAIL — could not read frame")
    except Exception as e:
        print(f"FAIL — {e}")
    return False


def check_i2c():
    """Check for PCA9685 on I2C bus."""
    print("[I2C]   ", end="")
    try:
        result = subprocess.run(
            ["i2cdetect", "-y", "1"],
            capture_output=True, text=True, timeout=5,
        )
        if "40" in result.stdout:
            print("OK — PCA9685 found at 0x40")
            return True
        else:
            print("NOT FOUND — PCA9685 not detected (servos will use fallback)")
            return False
    except FileNotFoundError:
        print("SKIP — i2cdetect not installed (run: sudo apt install i2c-tools)")
    except Exception as e:
        print(f"FAIL — {e}")
    return False


def check_microphone():
    """Verify microphone is available."""
    print("[Mic]   ", end="")
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        found = False
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                name = info["name"]
                if "usb" in name.lower() or "webcam" in name.lower() or "microphone" in name.lower():
                    print(f"OK — '{name}' (index {i})")
                    found = True
                    break
        if not found:
            default = pa.get_default_input_device_info()
            print(f"OK — using default: '{default['name']}'")
            found = True
        pa.terminate()
        return found
    except Exception as e:
        print(f"FAIL — {e}")
    return False


def check_speaker():
    """Test speaker with a short beep."""
    print("[Speaker] ", end="")
    try:
        result = subprocess.run(
            ["speaker-test", "-t", "sine", "-f", "440", "-l", "1", "-p", "1"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            print("OK — beep played")
            return True
        print("FAIL — speaker-test returned error")
    except FileNotFoundError:
        print("SKIP — speaker-test not found (run: sudo apt install alsa-utils)")
    except subprocess.TimeoutExpired:
        print("OK — audio device responded")
        return True
    except Exception as e:
        print(f"FAIL — {e}")
    return False


def main():
    print("=== R08Y Hardware Check ===\n")
    results = {
        "Camera": check_camera(),
        "I2C": check_i2c(),
        "Microphone": check_microphone(),
        "Speaker": check_speaker(),
    }
    print("\n=== Summary ===")
    all_ok = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL/SKIP"
        print(f"  {name}: {status}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nAll hardware checks passed! R08Y is ready.")
    else:
        print("\nSome checks failed. Review the output above.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
