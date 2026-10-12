"""The fake-engine fixture is stable and content independent."""


def test_fake_engine_produces_repeatable_output(fake_engine) -> None:
    first = fake_engine.complete("private prompt one")
    second = fake_engine.complete("private prompt two")

    assert first == second
    assert first.text == "deterministic fake response"
    assert first.input_tokens == 4
    assert first.output_tokens == 4
    assert fake_engine.stream("anything") == (
        "deterministic",
        " fake",
        " response",
    )
