"""Patrol Routes skill — saved scan patterns for security monitoring.

Voice commands: "start patrol", "stop patrol", "save this position"
Events emitted: patrol.started, patrol.stopped, patrol.waypoint
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


@property
def _routes_file(self) -> Path:
    return Path(self.ctx.data_dir) / "routes.json"


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._patrolling = False
        self._current_route: Optional[str] = None
        self._saved_positions: List[Tuple[float, float]] = []
        self._routes: Dict[str, List[dict]] = {}

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="patrol_routes",
            version="1.0.0",
            description="Saved patrol routes and security scan patterns",
            requires={"servos"},
            commands=[
                VoiceCommand(triggers=["start patrol", "begin patrol", "patrol mode"], handler="cmd_start"),
                VoiceCommand(triggers=["stop patrol", "end patrol"], handler="cmd_stop"),
                VoiceCommand(triggers=["save this position", "mark position", "remember this spot"], handler="cmd_save_pos"),
                VoiceCommand(triggers=["clear route", "reset route"], handler="cmd_clear"),
                VoiceCommand(triggers=["list routes", "what routes"], handler="cmd_list"),
            ],
            emits_events=["patrol.started", "patrol.stopped", "patrol.waypoint"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._load_routes()

    def _load_routes(self):
        routes_file = Path(self.ctx.data_dir) / "routes.json"
        if routes_file.exists():
            self._routes = json.loads(routes_file.read_text())

    def _save_routes(self):
        routes_file = Path(self.ctx.data_dir) / "routes.json"
        routes_file.write_text(json.dumps(self._routes, indent=2))

    async def cmd_start(self, text: str, user_name: str) -> Optional[str]:
        if self._patrolling:
            return "I'm already patrolling."
        route_name = "default"
        waypoints = self._routes.get(route_name)
        if not waypoints or len(waypoints) < 2:
            return "No patrol route saved. Say 'save this position' at each spot first."
        self._patrolling = True
        self._current_route = route_name
        self._spawn_task(self._patrol_loop(waypoints))
        await self.ctx.event_bus.emit("patrol.started", source=self.name, route=route_name)
        return f"Starting patrol with {len(waypoints)} waypoints."

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        if not self._patrolling:
            return "I'm not patrolling right now."
        self._patrolling = False
        await self.ctx.event_bus.emit("patrol.stopped", source=self.name)
        return "Patrol stopped."

    async def cmd_save_pos(self, text: str, user_name: str) -> Optional[str]:
        pan = await self.ctx.servo.get_angle(0)
        tilt = await self.ctx.servo.get_angle(1)
        if "default" not in self._routes:
            self._routes["default"] = []
        self._routes["default"].append({"pan": pan, "tilt": tilt, "dwell": 3.0})
        self._save_routes()
        count = len(self._routes["default"])
        return f"Position saved! Waypoint {count}: pan {pan:.0f}, tilt {tilt:.0f}."

    async def cmd_clear(self, text: str, user_name: str) -> Optional[str]:
        self._routes["default"] = []
        self._save_routes()
        return "Patrol route cleared."

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        if not self._routes:
            return "No patrol routes saved yet."
        parts = []
        for name, waypoints in self._routes.items():
            parts.append(f"{name}: {len(waypoints)} waypoints")
        return "Patrol routes: " + ", ".join(parts)

    async def _patrol_loop(self, waypoints: List[dict]):
        """Cycle through waypoints continuously."""
        while self._patrolling and self._running:
            for i, wp in enumerate(waypoints):
                if not self._patrolling:
                    break
                await self.ctx.servo.set_angle(0, wp["pan"])
                await self.ctx.servo.set_angle(1, wp["tilt"])
                await self.ctx.event_bus.emit(
                    "patrol.waypoint", source=self.name, index=i, pan=wp["pan"], tilt=wp["tilt"]
                )
                await asyncio.sleep(wp.get("dwell", 3.0))
