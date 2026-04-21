"""Home Assistant skill — control smart home devices via HA REST API.

Configure your Home Assistant URL and long-lived access token in config.yaml
under skills.home_assistant.
"""

import json
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._ha_url = ""
        self._ha_token = ""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="home_assistant",
            version="1.0.0",
            description="Control smart home devices via Home Assistant API",
            requires={"network"},
            commands=[
                VoiceCommand(triggers=["turn on the", "turn on"], handler="cmd_turn_on"),
                VoiceCommand(triggers=["turn off the", "turn off"], handler="cmd_turn_off"),
                VoiceCommand(triggers=["toggle the", "toggle"], handler="cmd_toggle"),
                VoiceCommand(triggers=["home status", "smart home status"], handler="cmd_status"),
            ],
        )

    async def setup(self, context):
        await super().setup(context)
        self._ha_url = self.ctx.config.get("url", "").rstrip("/")
        self._ha_token = self.ctx.config.get("token", "")
        if not self._ha_url or not self._ha_token or self._ha_token.startswith("YOUR_"):
            self.ctx.logger.info("Home Assistant not configured — skill available but inactive")

    def _ha_request(self, method: str, path: str, data: Optional[dict] = None) -> Optional[dict]:
        """Make an authenticated request to Home Assistant."""
        if not self._ha_url or not self._ha_token:
            return None
        url = f"{self._ha_url}/api/{path}"
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self._ha_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except (URLError, json.JSONDecodeError, OSError) as e:
            self.ctx.logger.error("Home Assistant request failed: %s", e)
            return None

    def _extract_entity(self, text: str) -> Optional[str]:
        """Try to extract an entity from voice command text.

        Maps common names to HA entity IDs. Override via config.
        """
        text_lower = text.lower()
        # Check custom entity map from config
        entity_map = self.ctx.config.get("entities", {})
        for friendly_name, entity_id in entity_map.items():
            if friendly_name.lower() in text_lower:
                return entity_id

        # Default guesses based on common names
        common = {
            "lights": "light.living_room",
            "light": "light.living_room",
            "bedroom light": "light.bedroom",
            "kitchen light": "light.kitchen",
            "fan": "switch.fan",
            "tv": "media_player.tv",
        }
        for name, entity in common.items():
            if name in text_lower:
                return entity
        return None

    async def _call_service(self, domain: str, service: str, entity_id: str) -> Optional[str]:
        if not self._ha_url or not self._ha_token:
            return "Home Assistant isn't configured. Add URL and token to the skills.home_assistant config."
        result = self._ha_request("POST", f"services/{domain}/{service}", {"entity_id": entity_id})
        if result is not None:
            friendly = entity_id.split(".")[-1].replace("_", " ")
            return f"Done! {service.replace('_', ' ')}d the {friendly}."
        return "I couldn't reach Home Assistant. Check the connection."

    async def cmd_turn_on(self, text: str, user_name: str) -> Optional[str]:
        entity = self._extract_entity(text)
        if not entity:
            return "I'm not sure which device you mean. Try 'turn on the lights'."
        domain = entity.split(".")[0]
        return await self._call_service(domain, "turn_on", entity)

    async def cmd_turn_off(self, text: str, user_name: str) -> Optional[str]:
        entity = self._extract_entity(text)
        if not entity:
            return "I'm not sure which device to turn off."
        domain = entity.split(".")[0]
        return await self._call_service(domain, "turn_off", entity)

    async def cmd_toggle(self, text: str, user_name: str) -> Optional[str]:
        entity = self._extract_entity(text)
        if not entity:
            return "I'm not sure which device to toggle."
        domain = entity.split(".")[0]
        return await self._call_service(domain, "toggle", entity)

    async def cmd_status(self, text: str, user_name: str) -> Optional[str]:
        if not self._ha_url or not self._ha_token:
            return "Home Assistant isn't configured."
        result = self._ha_request("GET", "")
        if result and "message" in result:
            return f"Home Assistant is connected: {result['message']}"
        return "I couldn't reach Home Assistant."
