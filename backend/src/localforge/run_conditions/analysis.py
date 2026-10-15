"""Prompt-cache evidence and repeat-run spread statistics."""

import hashlib
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from localforge.core.models import JsonValue

PROMPT_CACHE_WARNING = "prompt_cache_likely"
PROMPT_TRUNCATED_WARNING = "prompt_truncated"
# An engine counting fewer than one token per 8 characters has almost certainly cut the prompt:
# ordinary text is about 4 characters per token or fewer.
TRUNCATION_CHARS_PER_COUNTED_TOKEN = 8
TRUNCATION_MIN_PROMPT_CHARS = 4000
FAST_TTFT_FRACTION = 0.2
CACHED_TOKEN_FRACTION = 0.5
UNSTABLE_CV = 0.10


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class PromptRepeatTracker:
    """Remember which request sequence numbers reused a prompt sent earlier in the run."""

    first_sequence: dict[str, int] = field(default_factory=dict)
    by_sequence: dict[int, tuple[str, bool]] = field(default_factory=dict)
    chars_by_sequence: dict[int, int] = field(default_factory=dict)

    def observe(self, prompt: str, sequence: int) -> str:
        digest = prompt_fingerprint(prompt)
        repeated = digest in self.first_sequence
        self.first_sequence.setdefault(digest, sequence)
        self.by_sequence[sequence] = (digest, repeated)
        self.chars_by_sequence[sequence] = len(prompt)
        return prompt


@dataclass(frozen=True)
class RequestObservation:
    sequence: int
    phase: str
    succeeded: bool
    ttft_ms: float | None
    prompt_hash: str | None
    repeated_prompt: bool
    prompt_tokens: int | None = None
    cached_input_tokens: int | None = None
    prompt_eval_count: int | None = None
    prompt_chars: int | None = None


def detect_prompt_truncation(
    observations: Sequence[RequestObservation],
) -> dict[str, JsonValue] | None:
    """Return one ``prompt_truncated`` warning when engines counted far too few prompt tokens.

    Engines such as Ollama cut a prompt to their context window without an error, so a run that
    meant to test an 8k-token prompt can silently test 2k. The prompt length in characters is
    known here; the engine's own token count is the evidence of what it actually read.
    """

    flagged: list[tuple[int, int, int]] = []
    for item in observations:
        if (
            item.phase == "measurement"
            and item.succeeded
            and item.prompt_chars is not None
            and item.prompt_chars >= TRUNCATION_MIN_PROMPT_CHARS
            and item.prompt_tokens is not None
            and item.prompt_tokens * TRUNCATION_CHARS_PER_COUNTED_TOKEN < item.prompt_chars
        ):
            flagged.append((item.sequence, item.prompt_chars, item.prompt_tokens))
    if not flagged:
        return None
    counted = int(statistics.median(tokens for _, _, tokens in flagged))
    sent_at_least = int(statistics.median(chars for _, chars, _ in flagged) / 4)
    return {
        "code": PROMPT_TRUNCATED_WARNING,
        "message": (
            f"{len(flagged)} measured request(s) sent prompts of roughly {sent_at_least:,} tokens "
            f"or more, but the engine counted only about {counted:,}. Its context window is "
            "probably too small and cut the prompt, so these results do not measure the "
            "intended context length. Raise the engine's context size (Ollama: num_ctx, "
            "llama-server: --ctx-size) and run again."
        ),
        "affected_requests": len(flagged),
        "estimated_sent_tokens": sent_at_least,
        "engine_counted_tokens": counted,
        "requests": [
            {"sequence": sequence, "prompt_chars": chars, "prompt_tokens": tokens}
            for sequence, chars, tokens in flagged[:50]
        ],
    }


def detect_prompt_cache(observations: Sequence[RequestObservation]) -> dict[str, JsonValue] | None:
    """Return one ``prompt_cache_likely`` warning, or None when there is no evidence.

    Evidence is either the engine reporting that at least half of a measured prompt came from
    its cache, a repeated prompt whose engine-reported evaluated-token count fell below half of
    the first occurrence's, or a repeated prompt whose TTFT is under 20% of the run median.
    """

    measured = [item for item in observations if item.phase == "measurement" and item.succeeded]
    ttfts = [item.ttft_ms for item in measured if item.ttft_ms is not None]
    median_ttft = statistics.median(ttfts) if ttfts else None
    first_eval: dict[str, int] = {}
    for item in sorted(observations, key=lambda value: value.sequence):
        if item.prompt_hash and item.prompt_eval_count is not None:
            first_eval.setdefault(item.prompt_hash, item.prompt_eval_count)

    flagged: list[JsonValue] = []
    reasons: set[str] = set()
    for item in measured:
        reason: str | None = None
        if (
            item.cached_input_tokens is not None
            and item.prompt_tokens
            and item.cached_input_tokens >= CACHED_TOKEN_FRACTION * item.prompt_tokens
        ):
            reason = "engine_reported_cached_tokens"
        elif (
            item.repeated_prompt
            and item.prompt_hash is not None
            and item.prompt_eval_count is not None
            and item.prompt_hash in first_eval
            and first_eval[item.prompt_hash] > 0
            and item.prompt_eval_count < CACHED_TOKEN_FRACTION * first_eval[item.prompt_hash]
        ):
            reason = "engine_reported_fewer_evaluated_tokens"
        elif (
            item.repeated_prompt
            and median_ttft is not None
            and median_ttft > 0
            and item.ttft_ms is not None
            and item.ttft_ms < FAST_TTFT_FRACTION * median_ttft
        ):
            reason = "repeated_prompt_fast_ttft"
        if reason is not None:
            reasons.add(reason)
            flagged.append(
                {
                    "sequence": item.sequence,
                    "reason": reason,
                    "ttft_ms": item.ttft_ms,
                    "cached_input_tokens": item.cached_input_tokens,
                    "prompt_tokens": item.prompt_tokens,
                }
            )
    if not flagged:
        return None
    return {
        "code": PROMPT_CACHE_WARNING,
        "message": (
            f"{len(flagged)} measured request(s) were likely answered from the engine's prompt "
            "cache, so their time to first token is faster than a fresh prompt would be."
        ),
        "affected_requests": len(flagged),
        "median_ttft_ms": median_ttft,
        "reasons": cast(JsonValue, sorted(reasons)),
        "requests": flagged[:50],
    }


def spread(values: Sequence[float]) -> dict[str, JsonValue]:
    """Median, range, and coefficient of variation (sample stdev / mean) of repeat values."""

    finite = [float(value) for value in values if math.isfinite(value)]
    if not finite:
        return {
            "count": 0,
            "median": None,
            "min": None,
            "max": None,
            "mean": None,
            "stdev": None,
            "cv": None,
            "unstable": None,
        }
    mean = statistics.fmean(finite)
    stdev = statistics.stdev(finite) if len(finite) >= 2 else None
    cv = stdev / abs(mean) if stdev is not None and mean != 0 else None
    return {
        "count": len(finite),
        "median": statistics.median(finite),
        "min": min(finite),
        "max": max(finite),
        "mean": mean,
        "stdev": stdev,
        "cv": cv,
        "unstable": cv > UNSTABLE_CV if cv is not None else None,
    }
