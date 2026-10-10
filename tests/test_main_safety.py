import asyncio

from r08y.main import _extract_name, _stop_runtime


class FakeComponent:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.stopped = False

    async def stop(self):
        self.stopped = True
        if self.fail:
            raise RuntimeError("test cleanup failure")


class FakeMobility(FakeComponent):
    def __init__(self, *, fail=False):
        super().__init__(fail=fail)
        self.emergency_stopped = False

    async def emergency_stop(self):
        self.emergency_stopped = True


def test_shutdown_stops_all_components_even_when_one_fails():
    async def run():
        mobility = FakeMobility()
        expression = FakeComponent()
        components = [FakeComponent(fail=True), *[FakeComponent() for _ in range(7)]]
        await _stop_runtime(
            skill_manager=None,
            camera=components[0],
            servo=components[1],
            mobility=mobility,
            distance=components[2],
            light_sensors=components[3],
            face_recognizer=components[4],
            voice_input=components[5],
            brain=components[6],
            tts=components[7],
            expression=expression,
        )
        assert mobility.emergency_stopped
        assert mobility.stopped
        assert expression.stopped
        assert all(component.stopped for component in components)

    asyncio.run(run())


def test_extract_name_from_spoken_registration():
    assert _extract_name("Remember my face, I'm Rob.") == "Rob"
    assert _extract_name("My name is Dr. Voss!") == "Dr. Voss"
    assert _extract_name("Remember my face") is None
