"""Battery telemetry for the Waveshare UPS HAT (D).

The HAT reports its two parallel 21700 cells through an INA219 at I2C address
0x43 (the Freenove board uses 0x40 and 0x48, so there is no conflict). Register
settings and the percentage formula follow Waveshare's published INA219.py
sample: 16 V range, gain /2, 12-bit x32 averaging, 0.01 ohm shunt.

R08Y only reads the INA219. It never writes to the HAT's power-management MCU
at 0x2D, so this module cannot arm auto-boot or cut power.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from r08y.config import PowerConfig

logger = logging.getLogger(__name__)

_REG_CONFIG = 0x00
_REG_BUS_VOLTAGE = 0x02
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Waveshare's set_calibration_16V_5A values for the UPS HAT (C)/(D) INA219.
_CALIBRATION = 26868
_CURRENT_LSB_MA = 0.1524
_CONFIG = (
    (0 << 13)       # 16 V bus range
    | (1 << 11)     # gain /2, 80 mV shunt range
    | (0x0D << 7)   # bus ADC 12-bit, 32 samples
    | (0x0D << 3)   # shunt ADC 12-bit, 32 samples
    | 0x07          # shunt and bus, continuous
)

# Waveshare maps a single-cell 3.0-4.2 V range onto 0-100 percent.
EMPTY_VOLTS = 3.0
FULL_VOLTS = 4.2


def battery_percent(volts: float) -> float:
    return max(0.0, min(100.0, (volts - EMPTY_VOLTS) / (FULL_VOLTS - EMPTY_VOLTS) * 100.0))


@dataclass(frozen=True)
class BatteryReading:
    volts: float
    current_ma: float  # negative while the cells are discharging
    percent: float

    @property
    def charging(self) -> bool:
        return self.current_ma > 0

    def describe(self) -> str:
        state = "charging" if self.charging else "on battery"
        return f"{self.percent:.0f}% ({self.volts:.2f} V, {state})"


class BatteryMonitor:
    """Base interface; the disabled monitor is also the default."""

    available = False
    detail = "UPS monitoring disabled"

    async def start(self) -> None:
        return

    async def read(self) -> Optional[BatteryReading]:
        return None

    async def stop(self) -> None:
        return


class MockBatteryMonitor(BatteryMonitor):
    def __init__(self, reading: Optional[BatteryReading] = None):
        self.reading = reading
        self.available = True
        self.detail = "mock battery"

    async def read(self) -> Optional[BatteryReading]:
        return self.reading


class WaveshareUPSHatD(BatteryMonitor):
    """Read-only INA219 adapter for the Waveshare UPS HAT (D)."""

    def __init__(self, config: PowerConfig):
        self._config = config
        self._bus = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ups")
        self.available = False
        self.detail = "UPS HAT (D) has not been opened"

    def _write(self, register: int, value: int) -> None:
        self._bus.write_i2c_block_data(
            self._config.i2c_address, register, [(value >> 8) & 0xFF, value & 0xFF]
        )

    def _read(self, register: int) -> int:
        high, low = self._bus.read_i2c_block_data(self._config.i2c_address, register, 2)
        return (high << 8) | low

    def _open(self) -> None:
        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus  # Debian python3-smbus
        self._bus = SMBus(self._config.i2c_bus)
        self._write(_REG_CALIBRATION, _CALIBRATION)
        self._write(_REG_CONFIG, _CONFIG)

    def _read_sync(self) -> BatteryReading:
        # A sudden load can reset the INA219, so restore calibration first,
        # as Waveshare's sample does before every current read.
        self._write(_REG_CALIBRATION, _CALIBRATION)
        volts = (self._read(_REG_BUS_VOLTAGE) >> 3) * 0.004
        raw_current = self._read(_REG_CURRENT)
        if raw_current > 32767:
            raw_current -= 65536
        return BatteryReading(
            volts=round(volts, 3),
            current_ma=round(raw_current * _CURRENT_LSB_MA, 1),
            percent=round(battery_percent(volts), 1),
        )

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._open)
            reading = await loop.run_in_executor(self._executor, self._read_sync)
        except Exception as exc:
            self.detail = (
                f"UPS HAT (D) not readable at I2C bus {self._config.i2c_bus} "
                f"address 0x{self._config.i2c_address:02x}: {type(exc).__name__}: {exc}"
            )
            logger.error(self.detail)
            return
        self.available = True
        self.detail = f"UPS HAT (D): {reading.describe()}"
        logger.info(self.detail)

    async def read(self) -> Optional[BatteryReading]:
        if not self.available:
            return None
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._read_sync)
        except Exception:
            logger.warning("UPS battery read failed", exc_info=True)
            return None

    async def stop(self) -> None:
        self.available = False
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                logger.debug("I2C close failed", exc_info=True)
            self._bus = None
        self._executor.shutdown(wait=False)


def create_battery_monitor(config: PowerConfig) -> BatteryMonitor:
    if not config.ups_enabled:
        return BatteryMonitor()
    if config.ups_model == "waveshare_ups_hat_d":
        return WaveshareUPSHatD(config)
    logger.warning("Unsupported UPS model %s; battery monitoring disabled", config.ups_model)
    return BatteryMonitor()


class BatteryWarnings:
    """Turn readings into one spoken warning per threshold crossing."""

    # Percent above a threshold before its warning can repeat.
    REARM_MARGIN = 5.0

    def __init__(self, config: PowerConfig):
        self._low = config.low_battery_percent
        self._critical = config.critical_battery_percent
        self._warned_low = False
        self._warned_critical = False

    def evaluate(self, reading: BatteryReading) -> Optional[str]:
        if reading.charging:
            self._warned_low = self._warned_critical = False
            return None
        if reading.percent > self._low + self.REARM_MARGIN:
            self._warned_low = False
        if reading.percent > self._critical + self.REARM_MARGIN:
            self._warned_critical = False

        if reading.percent <= self._critical and not self._warned_critical:
            self._warned_critical = self._warned_low = True
            return (
                f"My battery is down to {reading.percent:.0f} percent. "
                "Please plug in my charger now."
            )
        if reading.percent <= self._low and not self._warned_low:
            self._warned_low = True
            return (
                f"Heads up, my battery is at {reading.percent:.0f} percent. "
                "I'll need a charge soon."
            )
        return None
