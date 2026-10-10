"""Report the Freenove chassis light-sensor readings."""

from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="light_sensors",
            version="1.0.0",
            description="Read the left and right chassis photoresistors",
            requires={"light_sensors"},
            commands=[
                VoiceCommand(
                    triggers=["check light sensors", "where is the light", "light levels"],
                    handler="cmd_read",
                )
            ],
        )

    async def cmd_read(self, text: str, user_name: str) -> Optional[str]:
        reading = await self.ctx.light_sensors.read()
        if reading is None:
            return "I couldn't read my light sensors."
        difference = reading.left_volts - reading.right_volts
        if abs(difference) < 0.12:
            direction = "The sensor voltages are balanced across both sides."
        elif difference > 0:
            direction = "The left sensor voltage is higher."
        else:
            direction = "The right sensor voltage is higher."
        return (
            f"Left light sensor: {reading.left_volts:.2f} volts. "
            f"Right: {reading.right_volts:.2f} volts. {direction}"
        )
