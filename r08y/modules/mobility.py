"""Mobility module stub for R08Y.

Placeholder for future wheel/motor control. Implements the MotorController
interface so the system architecture is ready when you add wheels.
"""

import logging

from r08y.modules.servo_controller import MotorController

logger = logging.getLogger(__name__)


class WheelController(MotorController):
    """Stub for future DC motor / stepper wheel control.

    TODO: Implement with L298N or similar motor driver when adding wheels.
    """

    async def set_angle(self, channel: int, angle: float) -> None:
        raise NotImplementedError("Wheel control not yet implemented")

    async def get_angle(self, channel: int) -> float:
        raise NotImplementedError("Wheel control not yet implemented")

    async def home(self) -> None:
        logger.debug("Wheel home — not implemented yet")

    async def start(self) -> None:
        logger.info("Mobility module loaded (not yet implemented)")

    async def stop(self) -> None:
        pass
