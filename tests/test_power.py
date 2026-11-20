"""Tests for Waveshare UPS HAT (D) battery telemetry."""

import asyncio

from r08y.config import PowerConfig, R08YConfig, validate_config
from r08y.modules.power import (
    BatteryReading,
    BatteryWarnings,
    WaveshareUPSHatD,
    battery_percent,
    create_battery_monitor,
)


class FakeBus:
    """INA219 registers as big-endian bytes, like the real chip."""

    def __init__(self, registers):
        self.registers = registers
        self.writes = []

    def write_i2c_block_data(self, address, register, data):
        self.writes.append((address, register, data))

    def read_i2c_block_data(self, address, register, length):
        assert address == 0x43 and length == 2
        value = self.registers[register]
        return [(value >> 8) & 0xFF, value & 0xFF]


def test_percent_uses_waveshare_single_cell_range():
    assert battery_percent(4.2) == 100.0
    assert battery_percent(3.6) == 50.0
    assert battery_percent(2.9) == 0.0


def test_reading_decodes_ina219_registers_and_restores_calibration():
    monitor = WaveshareUPSHatD(PowerConfig(ups_enabled=True, ups_model="waveshare_ups_hat_d"))
    # 3.9 V on the bus register (4 mV LSB, shifted 3) and -500 mA current.
    monitor._bus = FakeBus({0x02: (975 << 3), 0x04: 65536 - 3281})

    reading = monitor._read_sync()

    assert reading.volts == 3.9
    assert reading.current_ma == -500.0
    assert reading.percent == 75.0
    assert not reading.charging
    assert monitor._bus.writes[0] == (0x43, 0x05, [0x68, 0xF4])  # 26868


def test_warnings_fire_once_per_threshold_and_rearm_when_charged():
    warnings = BatteryWarnings(PowerConfig(low_battery_percent=20, critical_battery_percent=8))

    def reading(percent, current_ma=-400.0):
        return BatteryReading(volts=3.5, current_ma=current_ma, percent=percent)

    assert warnings.evaluate(reading(50)) is None
    assert "20 percent" in warnings.evaluate(reading(20))
    assert warnings.evaluate(reading(18)) is None
    assert "charger now" in warnings.evaluate(reading(7))
    assert warnings.evaluate(reading(6)) is None
    assert warnings.evaluate(reading(30, current_ma=900.0)) is None
    assert "19 percent" in warnings.evaluate(reading(19))


def test_disabled_ups_is_not_opened_and_model_is_validated():
    assert not create_battery_monitor(PowerConfig()).available
    config = R08YConfig(power=PowerConfig(ups_enabled=True, ups_model="other"))
    assert any("ups_model" in error for error in validate_config(config))
    config.power.ups_model = "waveshare_ups_hat_d"
    assert validate_config(config) == []


def test_unreadable_hat_reports_the_address():
    async def run():
        monitor = WaveshareUPSHatD(
            PowerConfig(ups_enabled=True, ups_model="waveshare_ups_hat_d", i2c_bus=99)
        )

        def fail():
            raise OSError("No such device")

        monitor._open = fail
        await monitor.start()
        assert not monitor.available
        assert "0x43" in monitor.detail
        await monitor.stop()

    asyncio.run(run())
