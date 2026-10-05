import asyncio
from types import SimpleNamespace

from r08y.config import R08YConfig
from r08y.modules import health
from r08y.modules.health import ComponentHealth, collect_runtime_health
from r08y.modules.sensors import MockDistanceSensor, MockLightSensors


def test_runtime_health_reports_exact_capabilities(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "discover_freenove_path", lambda _path: tmp_path)
    monkeypatch.setattr(health, "_network_available", lambda: True)
    monkeypatch.setattr(health, "_read_throttle", lambda: ComponentHealth("pass", "throttled=0x0"))

    async def run():
        distance = MockDistanceSensor(88.0)
        lights = MockLightSensors(1.2, 1.3)
        await distance.start()
        await lights.start()
        report = await collect_runtime_health(
            R08YConfig(),
            camera=SimpleNamespace(available=True, backend="opencv"),
            servo=SimpleNamespace(available=True),
            voice_input=SimpleNamespace(available=True, device_name="XVF3800"),
            tts=SimpleNamespace(playback_available=True),
            distance=distance,
            light_sensors=lights,
            mobility=SimpleNamespace(available=True),
        )
        assert report.ready
        assert report.components["ultrasonic"].detail == "88.0 cm"
        assert report.components["speaker_path"].status == "pass"
        assert report.components["ups"].status == "skip"
        assert "mobility=pass" in report.summary()

    asyncio.run(run())
