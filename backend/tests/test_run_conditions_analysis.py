"""Prompt-cache warnings, unique synthetic prompts, and repeat-spread statistics."""

import math

import pytest

from localforge.core.models import PromptSizingMethod, Workload, WorkloadInputMode, WorkloadKind
from localforge.run_conditions.analysis import (
    PromptRepeatTracker,
    RequestObservation,
    detect_prompt_cache,
    spread,
)
from localforge.worker.execution import _synthetic_prompt


def _observation(
    sequence: int,
    ttft: float,
    *,
    repeated: bool = False,
    prompt_hash: str | None = None,
    phase: str = "measurement",
    **extra: int | None,
) -> RequestObservation:
    return RequestObservation(
        sequence=sequence,
        phase=phase,
        succeeded=True,
        ttft_ms=ttft,
        prompt_hash=prompt_hash or f"hash-{sequence}",
        repeated_prompt=repeated,
        **extra,
    )


def test_repeated_prompt_with_very_fast_ttft_is_flagged() -> None:
    observations = [
        _observation(0, 3800, phase="warmup", prompt_hash="same"),
        _observation(1, 120, repeated=True, prompt_hash="same"),
        _observation(2, 3700),
        _observation(3, 3900),
    ]
    warning = detect_prompt_cache(observations)
    assert warning is not None
    assert warning["code"] == "prompt_cache_likely"
    assert warning["affected_requests"] == 1
    assert warning["reasons"] == ["repeated_prompt_fast_ttft"]
    assert warning["requests"][0]["sequence"] == 1


def test_fast_but_unique_prompts_are_not_flagged() -> None:
    observations = [_observation(0, 100), _observation(1, 3800), _observation(2, 3900)]
    assert detect_prompt_cache(observations) is None


def test_engine_reported_cached_tokens_are_flagged() -> None:
    observations = [
        _observation(0, 900, prompt_tokens=43, cached_input_tokens=0),
        _observation(1, 850, prompt_tokens=43, cached_input_tokens=42),
    ]
    warning = detect_prompt_cache(observations)
    assert warning is not None
    assert warning["reasons"] == ["engine_reported_cached_tokens"]


def test_ollama_fewer_evaluated_tokens_on_a_repeat_is_flagged() -> None:
    observations = [
        _observation(0, 900, prompt_hash="p", prompt_eval_count=512),
        _observation(1, 850, repeated=True, prompt_hash="p", prompt_eval_count=12),
    ]
    warning = detect_prompt_cache(observations)
    assert warning is not None
    assert warning["reasons"] == ["engine_reported_fewer_evaluated_tokens"]


def test_tracker_marks_repeats_across_warmup_and_measurement() -> None:
    tracker = PromptRepeatTracker()
    tracker.observe("hello", 0)
    tracker.observe("world", 1)
    tracker.observe("hello", 2)
    assert tracker.by_sequence[0][1] is False
    assert tracker.by_sequence[2][1] is True
    assert tracker.by_sequence[0][0] == tracker.by_sequence[2][0]


def _workload(method: PromptSizingMethod, target: int | None = 64) -> Workload:
    return Workload(
        name="w",
        kind=WorkloadKind.COMPLETION,
        input_mode=WorkloadInputMode.SYNTHETIC,
        target_input_tokens=target,
        prompt_sizing_method=method,
    )


def test_synthetic_prompt_marker_starts_the_prompt_and_keeps_the_size() -> None:
    workload = _workload(PromptSizingMethod.CHARACTERS)
    plain = _synthetic_prompt(workload, 0)
    first = _synthetic_prompt(workload, 0, marker="lf-abcd1234-00000")
    second = _synthetic_prompt(workload, 0, marker="lf-abcd1234-00001")
    assert first.startswith("[lf-abcd1234-00000] ")
    assert first != second
    assert len(first) == len(plain) == 64 * 4
    words = _synthetic_prompt(_workload(PromptSizingMethod.WORDS, 5), 3, marker="lf-abcd1234-00002")
    assert words.startswith("[lf-abcd1234-00002] ")
    assert len(words.split()) == 6


def test_workload_defaults_to_unique_prompts() -> None:
    assert _workload(PromptSizingMethod.CHARACTERS).repeat_prompts is False


def test_spread_reports_median_range_and_coefficient_of_variation() -> None:
    result = spread([10.0, 12.0, 14.0])
    assert result["median"] == 12.0
    assert result["min"] == 10.0 and result["max"] == 14.0
    assert result["stdev"] == pytest.approx(2.0)
    assert result["cv"] == pytest.approx(2.0 / 12.0)
    assert result["unstable"] is True

    stable = spread([100.0, 101.0, 99.0])
    assert stable["cv"] == pytest.approx(0.01)
    assert stable["unstable"] is False

    single = spread([5.0])
    assert single["cv"] is None and single["unstable"] is None and single["median"] == 5.0
    assert spread([])["count"] == 0
    assert spread([math.nan, 4.0])["count"] == 1


def test_a_prompt_the_engine_cut_short_is_flagged_as_truncated() -> None:
    from localforge.run_conditions.analysis import RequestObservation, detect_prompt_truncation

    def observation(
        sequence: int, *, chars: int, counted: int, phase: str = "measurement"
    ) -> object:
        return RequestObservation(
            sequence=sequence,
            phase=phase,
            succeeded=True,
            ttft_ms=100.0,
            prompt_hash=None,
            repeated_prompt=False,
            prompt_tokens=counted,
            prompt_chars=chars,
        )

    # An 8k-token prompt (~33k characters) that Ollama cut to its 2,050-token window.
    cut = detect_prompt_truncation(
        [observation(3, chars=33_000, counted=2_050), observation(4, chars=33_000, counted=2_050)]  # type: ignore[list-item]
    )
    assert cut is not None
    assert cut["code"] == "prompt_truncated"
    assert cut["affected_requests"] == 2
    assert cut["engine_counted_tokens"] == 2_050
    assert "context window is probably too small" in str(cut["message"])

    # A prompt counted at its full length, a short prompt, and a warmup request are not flagged.
    assert detect_prompt_truncation([observation(3, chars=33_000, counted=8_200)]) is None  # type: ignore[list-item]
    assert detect_prompt_truncation([observation(3, chars=1_000, counted=20)]) is None  # type: ignore[list-item]
    assert (
        detect_prompt_truncation([observation(1, chars=33_000, counted=2_050, phase="warmup")])  # type: ignore[list-item]
        is None
    )
