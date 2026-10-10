import asyncio
from types import SimpleNamespace

from r08y.config import TTSConfig
from r08y.skills.voice_profiles import Skill


def test_default_profile_restores_warm_droid_voice_and_tone():
    skill = Skill()
    tts_config = TTSConfig()
    skill._context = SimpleNamespace(tts=SimpleNamespace(_config=tts_config))

    asyncio.run(skill._switch_profile("pirate"))
    assert tts_config.pitch == "+0Hz"

    result = asyncio.run(skill._switch_profile("default"))

    assert result == "Switched to default voice."
    assert tts_config.voice == "en-US-AndrewMultilingualNeural"
    assert tts_config.rate == "+4%"
    assert tts_config.pitch == "+2Hz"
