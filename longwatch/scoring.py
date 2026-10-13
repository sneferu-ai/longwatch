"""Scoring and sentences (§8.4).

Integer 0–100 = pattern ``weight`` scaled by margin factors (how far past each
threshold), clamped [1, 100].  Only fired patterns create candidates.  Every
candidate carries one sentence rendered by substituting measured values into
the template — deterministic string formatting from integers, no float parsing
anywhere.  The rendered sentence is vocabulary-checked as an invariant before
persist.
"""
from __future__ import annotations

import re

from . import canonical, rules as rules_mod, vocab

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

#: The complete placeholder vocabulary.  A template naming anything else is
#: refused at write time (422), so a render-time breach cannot happen.
PLACEHOLDERS: dict[str, str] = {
    "animal": "the detected animal class name",
    "class": "the detected class name",
    "dur": "duration of the qualifying span, integer seconds",
    "motion": "mean motion energy over the span, 3-decimal string",
    "tol": "gap tolerance used, integer seconds",
    "pdur": "person overlap with the span, integer seconds",
    "gap": "silence gap duration, integer seconds",
    "value": "a measured value, 3-decimal string",
    "p": "the percentile used",
    "samples": "the detector sample count",
}


class TemplateError(ValueError):
    """A sentence template is malformed or names an unknown placeholder."""


class RenderError(RuntimeError):
    """A template could not be rendered from the measured evidence."""


def validate_template(template: str) -> None:
    if not isinstance(template, str) or not template.strip():
        raise TemplateError("sentence_template must be a non-empty string")
    for name in _PLACEHOLDER_RE.findall(template):
        if name not in PLACEHOLDERS:
            raise TemplateError(
                f"unknown placeholder {{{name}}}; supported: {sorted(PLACEHOLDERS)}"
            )


def _resolve(name: str, evidence: dict) -> str | None:
    if name == "dur":
        if "duration_ms" in evidence:
            return canonical.ms_to_seconds_str(evidence["duration_ms"])
        return None
    if name == "gap":
        # the silence gap's own duration, not the qualifying span's
        if "gap_ms" in evidence:
            return canonical.ms_to_seconds_str(evidence["gap_ms"])
        if "duration_ms" in evidence:
            return canonical.ms_to_seconds_str(evidence["duration_ms"])
        return None
    if name == "motion":
        if "motion_mean_milli" in evidence:
            return canonical.milli_str(evidence["motion_mean_milli"])
        return None
    if name == "tol":
        return canonical.ms_to_seconds_str(evidence.get("gap_tolerance_ms", 0))
    if name == "pdur":
        if "overlap_ms" in evidence:
            return canonical.ms_to_seconds_str(evidence["overlap_ms"])
        return None
    if name == "animal":
        return evidence.get("class") or evidence.get("overlap_class")
    if name == "class":
        return evidence.get("class")
    if name == "value":
        for key in ("value_max_milli", "conf_max_milli", "threshold_milli"):
            if key in evidence:
                return canonical.milli_str(evidence[key])
        return None
    if name == "p":
        if "percentile" in evidence:
            return str(int(evidence["percentile"]))
        for key, value in evidence.items():
            if key.endswith("_percentile"):
                return str(int(value))
        return None
    if name == "samples":
        return str(int(evidence.get("samples", 0)))
    return None


def render_sentence(template: str, evidence: dict) -> str:
    """Substitute measured values; raise :class:`RenderError` when unrenderable."""
    validate_template(template)

    def replace(match: re.Match) -> str:
        name = match.group(1)
        value = _resolve(name, evidence)
        if value is None:
            raise RenderError(f"placeholder {{{name}}} has no measured value in evidence")
        return value

    sentence = _PLACEHOLDER_RE.sub(replace, template).strip()
    if not sentence:
        raise RenderError("rendered sentence is empty")
    return sentence


def _round_half_even(value: float) -> int:
    import math

    floor = math.floor(value)
    remainder = value - floor
    if remainder > 0.5:
        return int(floor) + 1
    if remainder < 0.5:
        return int(floor)
    return int(floor) if floor % 2 == 0 else int(floor) + 1


def margin_factor(evidence: dict) -> float:
    """How far past each threshold the span is, as a bounded multiplier."""
    ratios: list[float] = []
    for got_key, required_key in (("duration_ms", "required_duration_ms"),
                                  ("held_ms", "required_hold_ms"),
                                  ("overlap_ms", "required_overlap_ms")):
        got = evidence.get(got_key)
        required = evidence.get(required_key)
        if got is None or not required:
            continue
        ratios.append(min(6.0, max(1.0, float(got) / float(required))))
    if not ratios:
        return 1.0
    return min(1.5, 0.9 + 0.1 * (sum(ratios) / len(ratios)))


def score_span(weight: int, evidence: dict) -> int:
    score = _round_half_even(float(weight) * margin_factor(evidence))
    return max(1, min(100, int(score)))


class Candidate:
    """One fired pattern on one window — sentence already vocabulary-checked."""

    def __init__(self, pattern: dict, span: rules_mod.Span, score: int, sentence: str,
                 evidence: dict):
        self.pattern = pattern
        self.span = span
        self.score = score
        self.sentence = sentence
        self.evidence = evidence


def evaluate_window(pattern: dict, ctx: rules_mod.EvalContext) -> list[Candidate]:
    """Fire ``pattern`` for the window in ``ctx`` (midpoint attribution)."""
    window = ctx.window
    result = rules_mod.evaluate(pattern["rule"], ctx)
    candidates: list[Candidate] = []
    for span in result.spans:
        # half-open attribution: a span whose midpoint lands exactly on a
        # window boundary belongs to the window that *starts* there — the
        # closed interval would attribute it to both and double the candidate
        if not (float(window["start_s"]) <= span.midpoint_s < float(window["end_s"])):
            continue
        evidence = dict(span.evidence)
        if result.notes.get("insufficient_samples"):
            evidence["insufficient_samples"] = True
            evidence["summary_samples"] = int(result.notes.get("samples", 0))
            evidence["required_samples"] = int(result.notes.get("required_samples", 0))
        sentence = render_sentence(pattern["sentence_template"], evidence)
        # The rendered-sentence vocabulary check is an invariant: a breach here
        # is impossible by construction (templates are guarded at write time)
        # and is treated as one anyway — the candidate is not persisted.
        vocab.check_text(sentence)
        evidence["span_start_ms"] = int(round(span.t_start * 1000.0))
        evidence["span_end_ms"] = int(round(span.t_end * 1000.0))
        candidates.append(Candidate(pattern, span, score_span(int(pattern["weight"]), evidence),
                                    sentence, evidence))
    return candidates
