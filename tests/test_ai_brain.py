from r08y.config import AIConfig
from r08y.modules.ai_brain import AIBrain


def test_system_prompt_keeps_spoken_style_separate_from_visual_context():
    brain = AIBrain(
        AIConfig(
            system_prompt="You are R08Y.",
            spoken_style="Use short, natural spoken replies.",
        )
    )

    prompt = brain._build_system_prompt("You are looking at Rob.")

    assert prompt.startswith("You are R08Y.")
    assert "[Spoken response style: Use short, natural spoken replies.]" in prompt
    assert prompt.endswith("[Visual context: You are looking at Rob.]")
