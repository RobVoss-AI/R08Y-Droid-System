#!/bin/bash
# R08Y Droid System — Raspberry Pi Setup Script
# Run: bash install.sh
#
# Prerequisites: Raspberry Pi OS Lite (64-bit) on Pi 4B
# This script installs all system dependencies, creates a Python venv,
# compiles dlib with NEON optimizations, and sets up systemd service.

set -e

INSTALL_DIR="/opt/r08y"
VENV_DIR="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== R08Y Droid System Installer ==="
echo ""

# Check we're on a Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi."
    echo "Continuing anyway (some hardware features won't work)."
    echo ""
fi

# Enable I2C for PCA9685 servo board
echo "[1/7] Enabling I2C..."
if command -v raspi-config &> /dev/null; then
    sudo raspi-config nonint do_i2c 0
    echo "  I2C enabled"
else
    echo "  raspi-config not found — skip I2C setup (enable manually)"
fi

# Install system dependencies
echo "[2/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip python3-venv python3-dev \
    cmake build-essential \
    libatlas-base-dev libopenblas-dev \
    libhdf5-dev libhdf5-serial-dev \
    libjpeg-dev libpng-dev libtiff-dev \
    portaudio19-dev \
    ffmpeg \
    mpv \
    alsa-utils \
    i2c-tools \
    git

# Increase swap for dlib compilation
echo "[3/7] Configuring swap for dlib compilation..."
ORIGINAL_SWAP=$(grep CONF_SWAPSIZE /etc/dphys-swapfile 2>/dev/null | cut -d= -f2 || echo "100")
if [ -f /etc/dphys-swapfile ]; then
    sudo sed -i "s/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/" /etc/dphys-swapfile
    sudo systemctl restart dphys-swapfile
    echo "  Swap increased to 2GB"
fi

# Create install directory and venv
echo "[4/7] Creating virtual environment..."
sudo mkdir -p "$INSTALL_DIR/data"
sudo chown -R "$USER:$USER" "$INSTALL_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install Python dependencies
echo "[5/7] Installing Python packages (this may take a while)..."
pip install --upgrade pip wheel setuptools
pip install numpy pyyaml aiosqlite
pip install opencv-python-headless pyaudio
pip install pvporcupine anthropic openai groq edge-tts
pip install adafruit-circuitpython-servokit adafruit-circuitpython-pca9685

# dlib + face_recognition (this is the slow one)
echo "[6/7] Compiling dlib with NEON optimizations (may take 30-60 minutes)..."
pip install dlib --no-cache-dir
pip install face_recognition

# Restore swap
if [ -f /etc/dphys-swapfile ]; then
    sudo sed -i "s/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=$ORIGINAL_SWAP/" /etc/dphys-swapfile
    sudo systemctl restart dphys-swapfile
    echo "  Swap restored to ${ORIGINAL_SWAP}MB"
fi

# Copy project files
echo "[7/7] Installing R08Y..."
cp -r "$SCRIPT_DIR/r08y" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

# Create config from example if it doesn't exist
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    echo "  Created config.yaml — edit it to add your API keys!"
fi

# Install systemd service
sudo cp "$SCRIPT_DIR/r08y.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable r08y

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/config.yaml with your API keys"
echo "  2. Test: cd $INSTALL_DIR && $VENV_DIR/bin/python -m r08y"
echo "  3. Run as service: sudo systemctl start r08y"
echo "  4. View logs: journalctl -u r08y -f"
echo ""
echo "Hardware checklist:"
echo "  - USB webcam connected"
echo "  - Speaker connected (USB or 3.5mm)"
echo "  - PCA9685 + servos wired to I2C (optional)"
echo "  - Verify I2C: sudo i2cdetect -y 1"
