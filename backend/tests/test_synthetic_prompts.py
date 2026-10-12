"""Deterministic synthetic prompt sizing used by local benchmark workloads."""

import pytest

from localforge.core.models import PromptSizingMethod, Workload, WorkloadInputMode, WorkloadKind
from localforge.worker.execution import RunSetupError, _synthetic_prompt


def _workload(*, method: PromptSizingMethod, target: int | None, seed: int = 0) -> Workload:
    return Workload(
        name="Synthetic prompt fixture",
        kind=WorkloadKind.CHAT,
        input_mode=WorkloadInputMode.SYNTHETIC,
        request_count=1,
        prompt_sizing_method=method,
        target_input_tokens=target,
        random_seed=seed,
    )


def test_character_based_synthetic_prompt_uses_a_repeatable_approximate_size() -> None:
    workload = _workload(method=PromptSizingMethod.CHARACTERS, target=16, seed=3)

    prompt = _synthetic_prompt(workload, index=2)

    assert len(prompt) == 64
    assert prompt == _synthetic_prompt(workload, index=2)
    assert prompt != _synthetic_prompt(
        _workload(method=PromptSizingMethod.CHARACTERS, target=16, seed=4), index=2
    )


def test_word_based_synthetic_prompt_uses_the_requested_word_count() -> None:
    workload = _workload(method=PromptSizingMethod.WORDS, target=23, seed=11)

    prompt = _synthetic_prompt(workload, index=0)

    assert len(prompt.split()) == 23
    assert prompt == _synthetic_prompt(workload, index=0)


def test_tokenizer_sizing_without_an_installed_tokenizer_is_not_substituted() -> None:
    workload = _workload(method=PromptSizingMethod.TOKENIZER, target=128)

    with pytest.raises(RunSetupError, match="none is installed"):
        _synthetic_prompt(workload, index=0)
