#!/usr/bin/env bash
# Install R08Y on Raspberry Pi OS or Debian 13 for Raspberry Pi.

set -Eeuo pipefail

INSTALL_DIR="/opt/r08y"
SERVICE_ENV_DIR="/etc/r08y"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R08Y_USER="${SUDO_USER:-${USER}}"
R08Y_GROUP="$(id -gn "${R08Y_USER}")"
R08Y_UID="$(id -u "${R08Y_USER}")"
R08Y_HOME="$(getent passwd "${R08Y_USER}" | cut -d: -f6)"

echo "R08Y installer"
echo "Install user: ${R08Y_USER}"

if [[ ! -r /proc/device-tree/model ]] || ! tr -d '\0' </proc/device-tree/model | grep -qi "Raspberry Pi"; then
    echo "WARNING: Raspberry Pi hardware was not detected."
fi

echo "[1/6] Installing system packages"
sudo apt-get update
sudo apt-get install -y \
    python3-pip python3-venv python3-dev \
    python3-opencv python3-pyaudio python3-smbus python3-gpiozero \
    libopenblas-dev portaudio19-dev \
    ffmpeg mpv alsa-utils i2c-tools pipewire-bin wireplumber git

for optional_package in python3-picamera2 python3-dlib; do
    if apt-cache show "${optional_package}" >/dev/null 2>&1; then
        sudo apt-get install -y "${optional_package}"
    else
        echo "Optional package unavailable: ${optional_package}"
    fi
done

echo "[2/6] Creating the runtime environment"
sudo install -d -o "${R08Y_USER}" -g "${R08Y_GROUP}" "${INSTALL_DIR}" "${INSTALL_DIR}/data"
if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    sudo -u "${R08Y_USER}" python3 -m venv --system-site-packages "${INSTALL_DIR}/venv"
fi
sudo -u "${R08Y_USER}" "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip wheel setuptools
sudo -u "${R08Y_USER}" "${INSTALL_DIR}/venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"

if "${INSTALL_DIR}/venv/bin/python" -c "import dlib" >/dev/null 2>&1; then
    sudo -u "${R08Y_USER}" "${INSTALL_DIR}/venv/bin/pip" install face_recognition
else
    echo "WARNING: dlib is unavailable. Face recognition will be disabled; face tracking still works."
fi

echo "[3/6] Copying R08Y"
sudo rm -rf "${INSTALL_DIR}/r08y"
sudo cp -R "${SCRIPT_DIR}/r08y" "${INSTALL_DIR}/r08y"
sudo cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
sudo chown -R "${R08Y_USER}:${R08Y_GROUP}" "${INSTALL_DIR}"

if [[ ! -f "${INSTALL_DIR}/config.yaml" ]]; then
    sudo install -o "${R08Y_USER}" -g "${R08Y_GROUP}" -m 640 \
        "${SCRIPT_DIR}/config.example.yaml" "${INSTALL_DIR}/config.yaml"
fi

echo "[4/6] Preparing environment variables"
sudo install -d -o root -g "${R08Y_GROUP}" -m 750 "${SERVICE_ENV_DIR}"
if [[ ! -f "${SERVICE_ENV_DIR}/r08y.env" ]]; then
    sudo install -o root -g "${R08Y_GROUP}" -m 640 \
        "${SCRIPT_DIR}/.env.example" "${SERVICE_ENV_DIR}/r08y.env"
fi

echo "[5/6] Installing the system service"
service_temp="$(mktemp)"
trap 'rm -f "${service_temp}"' EXIT
sed \
    -e "s|@R08Y_USER@|${R08Y_USER}|g" \
    -e "s|@R08Y_GROUP@|${R08Y_GROUP}|g" \
    -e "s|@R08Y_UID@|${R08Y_UID}|g" \
    -e "s|@R08Y_INSTALL_DIR@|${INSTALL_DIR}|g" \
    "${SCRIPT_DIR}/r08y.service" >"${service_temp}"
sudo install -m 644 "${service_temp}" /etc/systemd/system/r08y.service
sudo systemctl daemon-reload
sudo loginctl enable-linger "${R08Y_USER}"
sudo systemctl enable r08y.service

for hardware_group in i2c video audio gpio; do
    if getent group "${hardware_group}" >/dev/null; then
        sudo usermod -aG "${hardware_group}" "${R08Y_USER}"
    fi
done

echo "[6/6] Checking the Freenove source path"
freenove_path="${R08Y_HOME}/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi/Code/Server"
if [[ -f "${freenove_path}/motor.py" ]]; then
    echo "Official Freenove modules found: ${freenove_path}"
else
    echo "WARNING: Official Freenove modules were not found at ${freenove_path}."
    echo "Clone the official FNK0043 repository before starting R08Y."
fi

echo
echo "Installation complete."
echo "1. Edit ${SERVICE_ENV_DIR}/r08y.env with API keys."
echo "2. Review ${INSTALL_DIR}/config.yaml."
echo "3. Reboot once if group membership or I2C settings changed."
echo "4. Passive check: cd ${INSTALL_DIR} && ./venv/bin/python -m r08y --smoke-test"
echo "5. Guided audio: ./venv/bin/python -m r08y --config config.yaml --audio-test"
echo "6. Speech audition: ./venv/bin/python -m r08y --config config.yaml --speech-test"
echo "7. Start: sudo systemctl start r08y"
echo "8. Logs: journalctl -u r08y -f"
