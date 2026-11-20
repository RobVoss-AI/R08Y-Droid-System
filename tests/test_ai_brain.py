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
    assert "[Personality: Curious, observant" in prompt
    assert "[Spoken response style: Use short, natural spoken replies.]" in prompt
    assert prompt.endswith("[Visual context: You are looking at Rob.]")


import asyncio
from datetime import datetime
from types import SimpleNamespace

from r08y.modules.ai_brain import REFUSAL_REPLY, ConversationTurn, VisualContext
from r08y.modules.robot_tools import ToolOutcome


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool(tool_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _response(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), stop_details=None)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def create(self, **request):
        request["messages"] = list(request["messages"])
        self.requests.append(request)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=FakeMessages(responses))


class FakeTools:
    def __init__(self):
        self.calls = []
        self.turns = 0

    def begin_turn(self):
        self.turns += 1

    def definitions(self):
        return [{"name": "look_with_camera", "input_schema": {"type": "object"}}]

    async def execute(self, name, tool_input, *, user_name="unknown"):
        self.calls.append((name, tool_input, user_name))
        return ToolOutcome([{"type": "text", "text": "photo"}])


def _brain(responses, **config):
    brain = AIBrain(AIConfig(api_key="test", **config))
    brain._client = FakeClient(responses)
    brain.available = True
    return brain


def test_tool_round_speaks_interim_words_runs_tool_and_answers():
    look = _tool("tool_1", "look_with_camera", {"question": "what is this?"})
    brain = _brain([
        _response("tool_use", _text("Let me look."), look),
        _response("end_turn", _text("A red mug.")),
    ])
    tools = FakeTools()
    interim = []

    async def on_interim(text):
        interim.append(text)

    reply = asyncio.run(
        brain.think(
            "What am I holding?",
            VisualContext(faces_visible=1, recognized_name="Rob", is_known=True),
            "Rob",
            tools=tools,
            on_interim_text=on_interim,
        )
    )

    assert reply == "A red mug."
    assert interim == ["Let me look."]
    assert tools.calls == [("look_with_camera", {"question": "what is this?"}, "Rob")]
    # Sonnet 5.5 requests go through the beta client with server-side fallback.
    first, second = brain._client.beta.messages.requests
    assert first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "low"}
    assert first["tools"] == tools.definitions()
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "look_with_camera" in first["system"][0]["text"]
    assert "Rob" in first["system"][1]["text"]
    assert second["messages"][-2]["content"][-1] is look
    assert second["messages"][-1]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "tool_1",
            "content": [{"type": "text", "text": "photo"}],
        }
    ]
    assert brain._histories["Rob"][-1].content == "Let me look. A red mug."


def test_refusal_is_spoken_as_a_short_decline():
    brain = _brain([_response("refusal")])

    reply = asyncio.run(brain.think("Do something unsafe", VisualContext()))

    assert reply == REFUSAL_REPLY


def test_unsupported_fallback_model_uses_standard_client():
    brain = _brain([_response("end_turn", _text("Hi."))], model="claude-haiku-5-5")

    assert asyncio.run(brain.think("Hello", VisualContext())) == "Hi."
    assert not brain._client.beta.messages.requests
    assert "fallbacks" not in brain._client.messages.requests[0]


def test_gesture_only_answer_returns_empty_text_and_is_not_saved():
    brain = _brain([
        _response("tool_use", _tool("tool_1", "look_with_camera", {})),
        _response("end_turn"),
    ])

    reply = asyncio.run(brain.think("Wave hello", VisualContext(), tools=FakeTools()))

    assert reply == ""
    assert [turn.role for turn in brain._histories["unknown"]] == ["user"]


def test_tool_rounds_stop_at_the_configured_limit():
    looping = [
        _response("tool_use", _text("Still looking."), _tool(f"t{i}", "look_with_camera", {}))
        for i in range(3)
    ]
    brain = _brain(looping, max_tool_rounds=2)

    reply = asyncio.run(brain.think("Look around", VisualContext(), tools=FakeTools()))

    assert reply == "Still looking."
    assert len(brain._client.beta.messages.requests) == 3


def test_trimmed_history_always_starts_with_a_user_turn():
    brain = _brain([_response("end_turn", _text("Sure."))], max_history_turns=2)
    brain._histories["unknown"] = [
        ConversationTurn("user", "one"),
        ConversationTurn("assistant", "two"),
    ]

    asyncio.run(brain.think("three", VisualContext()))

    messages = brain._client.beta.messages.requests[0]["messages"]
    assert messages == [{"role": "user", "content": "three"}]


def test_context_note_reports_local_time():
    note = AIBrain._context_note(
        VisualContext(), now=datetime(2026, 11, 20, 15, 5)
    )

    assert "You don't see anyone right now." in note
    assert "Friday November 20, 2026, 03:05 PM" in note
