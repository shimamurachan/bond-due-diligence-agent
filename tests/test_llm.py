from app.services.llm import _output_text


def test_output_text_ignores_null_response_items() -> None:
    payload = {
        "output": [
            None,
            {"type": "reasoning", "content": None},
            {
                "type": "message",
                "content": [None, {"type": "output_text", "text": '{"issuer":"测试主体","facts":[]}' }],
            },
        ]
    }
    assert _output_text(payload) == '{"issuer":"测试主体","facts":[]}'


def test_output_text_returns_empty_for_invalid_payload_shape() -> None:
    assert _output_text({"output": None}) == ""
