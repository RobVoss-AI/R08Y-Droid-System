"""Voice Profiles skill — switch TTS voice personalities.

Voice commands: "speak like a pirate", "serious voice", "normal voice"
Transforms AI responses through the VOICE_POST_AI pipeline hook.
"""

from typing import Dict, Optional

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand


PROFILES: Dict[str, dict] = {
    "default": {
        "voice": "en-US-AndrewMultilingualNeural",
        "rate": "+4%",
        "pitch": "+2Hz",
        "transform": None,
    },
    "pirate": {
        "voice": "en-US-GuyNeural",
        "rate": "-10%",
        "pitch": "+0Hz",
        "transform": "pirate",
    },
    "serious": {
        "voice": "en-US-AriaNeural",
        "rate": "-5%",
        "pitch": "+0Hz",
        "transform": None,
    },
    "cheerful": {
        "voice": "en-US-JennyNeural",
        "rate": "+10%",
        "pitch": "+2Hz",
        "transform": None,
    },
    "british": {
        "voice": "en-GB-RyanNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
        "transform": None,
    },
}


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._active_profile = "default"

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="voice_profiles",
            version="1.0.0",
            description="Switch TTS voice personalities",
            requires={"tts"},
            pipeline_hooks=[PipelineHook.VOICE_POST_AI],
            commands=[
                VoiceCommand(triggers=["speak like a pirate", "pirate voice"], handler="cmd_pirate"),
                VoiceCommand(triggers=["serious voice", "be serious"], handler="cmd_serious"),
                VoiceCommand(triggers=["cheerful voice", "be cheerful", "happy voice"], handler="cmd_cheerful"),
                VoiceCommand(triggers=["british voice", "speak british"], handler="cmd_british"),
                VoiceCommand(triggers=["normal voice", "default voice", "regular voice"], handler="cmd_default"),
                VoiceCommand(triggers=["what voices", "list voices"], handler="cmd_list"),
            ],
        )

    async def _switch_profile(self, name: str) -> str:
        profile = PROFILES.get(name)
        if not profile:
            return f"I don't know the voice '{name}'."
        self._active_profile = name
        # Update TTS config for next speech
        self.ctx.tts._config.voice = profile["voice"]
        self.ctx.tts._config.rate = profile["rate"]
        self.ctx.tts._config.pitch = profile["pitch"]
        return f"Switched to {name} voice."

    async def on_voice_post_ai(self, response: str) -> str:
        """Apply active profile transformation to AI response."""
        profile = PROFILES.get(self._active_profile, {})
        if profile.get("transform") == "pirate":
            response = response.replace("hello", "ahoy")
            response = response.replace("yes", "aye")
            response = response.replace("my friend", "me hearty")
            response = response.replace("okay", "aye aye")
        return response

    async def cmd_pirate(self, text: str, user_name: str) -> Optional[str]:
        return await self._switch_profile("pirate")

    async def cmd_serious(self, text: str, user_name: str) -> Optional[str]:
        return await self._switch_profile("serious")

    async def cmd_cheerful(self, text: str, user_name: str) -> Optional[str]:
        return await self._switch_profile("cheerful")

    async def cmd_british(self, text: str, user_name: str) -> Optional[str]:
        return await self._switch_profile("british")

    async def cmd_default(self, text: str, user_name: str) -> Optional[str]:
        return await self._switch_profile("default")

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        names = ", ".join(PROFILES.keys())
        return f"Available voices: {names}. Currently using {self._active_profile}."
