"""Small deterministic fake engine for ordinary tests and CI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeResponse:
    text: str
    input_tokens: int
    output_tokens: int


class DeterministicFakeEngine:
    """Return fixed output and token counts; this fixture never runs a model."""

    def complete(self, prompt: str) -> FakeResponse:
        del prompt  # Content is intentionally not retained by the fixture.
        return FakeResponse(text="deterministic fake response", input_tokens=4, output_tokens=4)

    def stream(self, prompt: str) -> tuple[str, ...]:
        response = self.complete(prompt)
        words = response.text.split(" ")
        return tuple(word if index == 0 else f" {word}" for index, word in enumerate(words))
