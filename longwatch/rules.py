"""The rule engine (§8.4) — absolute and per-source-relative semantics.

Condition types: ``presence`` (class/class_group, min_duration, min_overlap,
max_motion_energy, gap_tolerance_s), ``absence``, ``signal_threshold``
(absolute), ``signal_percentile`` (per-source-relative, read from
``sources.signal_summary``), and ``all``/``any`` combinators.

Presence conditions evaluate over the source-wide presence timeline — intervals
merged across window boundaries — so a 90 s requirement can fire even where
scene cuts produced shorter windows.  The candidate attributes to the window
containing the qualifying span's midpoint.

Every value that leaves this module is an integer in milli-units.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import classmap as classmap_mod

ALLOWED_CONDITION_TYPES = ("presence", "absence", "signal_threshold",
                           "signal_percentile", "all", "any")
ALLOWED_SIGNALS = ("motion", "luma", "persistence", "silence_gap")
MIN_SUMMARY_SAMPLES = 60


class RuleError(ValueError):
    """A pattern rule failed validation — named, closed, no guessing."""


@dataclass
class EvalContext:
    window: dict
    intervals: list[dict] = field(default_factory=list)
    series: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)


@dataclass
class Span:
    t_start: float
    t_end: float
    evidence: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.t_end - self.t_start)

    @property
    def midpoint_s(self) -> float:
        return (self.t_start + self.t_end) / 2.0


@dataclass
class EvalResult:
    spans: list[Span] = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    ok: bool = True


# --------------------------------------------------------------------- schema
def pattern_schema() -> dict:
    """The JSON Schema published at ``GET /api/v1/patterns/schema``."""
    condition = {
        "oneOf": [
            {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {"const": "presence"},
                    "anchor": {"type": "boolean"},
                    "class": {"enum": list(classmap_mod.RETAINED_NAMES)},
                    "class_group": {"enum": sorted(classmap_mod.CLASS_GROUPS)},
                    "min_duration_s": {"type": "number", "minimum": 0},
                    "min_overlap_s": {"type": "number", "minimum": 0},
                    "max_motion_energy": {"type": "number", "minimum": 0, "maximum": 1},
                    "gap_tolerance_s": {"type": "number", "minimum": 0},
                },
            },
            {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {"const": "absence"},
                    "anchor": {"type": "boolean"},
                    "class": {"enum": list(classmap_mod.RETAINED_NAMES)},
                    "class_group": {"enum": sorted(classmap_mod.CLASS_GROUPS)},
                },
            },
            {
                "type": "object",
                "required": ["type", "signal"],
                "properties": {
                    "type": {"const": "signal_threshold"},
                    "anchor": {"type": "boolean"},
                    "signal": {"enum": list(ALLOWED_SIGNALS)},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "min_duration_s": {"type": "number", "minimum": 0},
                },
            },
            {
                "type": "object",
                "required": ["type", "signal", "percentile", "op"],
                "properties": {
                    "type": {"const": "signal_percentile"},
                    "anchor": {"type": "boolean"},
                    "signal": {"enum": list(ALLOWED_SIGNALS)},
                    "percentile": {"enum": [10, 25, 50, 75, 85, 90, 95]},
                    "op": {"enum": ["gte", "lte"]},
                    "min_duration_s": {"type": "number", "minimum": 0},
                },
            },
            {
                "type": "object",
                "required": ["type", "of"],
                "properties": {
                    "type": {"enum": ["all", "any"]},
                    "of": {"type": "array", "minItems": 1, "items": {"$ref": "#/definitions/condition"}},
                },
            },
        ]
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LongWatch pattern rule",
        "type": "object",
        "required": ["slug", "version", "title", "sentence_template", "rule", "weight"],
        "properties": {
            "slug": {"type": "string", "pattern": "^[a-z0-9_]{3,64}$"},
            "version": {"type": "integer", "minimum": 1},
            "title": {"type": "string", "minLength": 1},
            "sentence_template": {"type": "string", "minLength": 1},
            "weight": {"type": "integer", "minimum": 1, "maximum": 100},
            "enabled": {"type": "boolean"},
            "rule": {"$ref": "#/definitions/condition"},
        },
        "definitions": {"condition": condition},
    }


def _validate_condition(condition: dict, path: str = "rule") -> None:
    if not isinstance(condition, dict):
        raise RuleError(f"{path}: a condition must be an object")
    kind = condition.get("type")
    if kind not in ALLOWED_CONDITION_TYPES:
        raise RuleError(f"{path}: unknown condition type {kind!r}")
    if kind in ("all", "any"):
        children = condition.get("of")
        if not isinstance(children, list) or not children:
            raise RuleError(f"{path}: {kind} needs a non-empty 'of' array")
        for index, child in enumerate(children):
            _validate_condition(child, f"{path}.of[{index}]")
        return
    if kind in ("presence", "absence"):
        named = [key for key in ("class", "class_group") if condition.get(key)]
        if len(named) != 1:
            raise RuleError(f"{path}: exactly one of 'class' or 'class_group' is required")
        if "class" in named and condition["class"] not in classmap_mod.RETAINED_NAMES:
            raise RuleError(
                f"{path}: class {condition['class']!r} is not a retained class; "
                "v0 admits no free-text class naming")
        if "class_group" in named and condition["class_group"] not in classmap_mod.CLASS_GROUPS:
            raise RuleError(
                f"{path}: class_group {condition['class_group']!r} is not one of "
                f"{sorted(classmap_mod.CLASS_GROUPS)}; v0 admits no free-text group naming")
        for numeric in ("min_duration_s", "min_overlap_s", "max_motion_energy", "gap_tolerance_s"):
            if numeric in condition and not isinstance(condition[numeric], (int, float)):
                raise RuleError(f"{path}: {numeric} must be a number")
        if kind == "presence" and not any(
                key in condition for key in ("min_duration_s", "min_overlap_s")):
            raise RuleError(f"{path}: presence needs min_duration_s or min_overlap_s")
        return
    signal = condition.get("signal")
    if signal not in ALLOWED_SIGNALS:
        raise RuleError(f"{path}: signal must be one of {list(ALLOWED_SIGNALS)}")
    if kind == "signal_threshold":
        if "min" not in condition and "max" not in condition:
            raise RuleError(f"{path}: signal_threshold needs min and/or max")
    else:
        if condition.get("op") not in ("gte", "lte"):
            raise RuleError(f"{path}: signal_percentile needs op gte|lte")
        if condition.get("percentile") not in (10, 25, 50, 75, 85, 90, 95):
            raise RuleError(f"{path}: signal_percentile needs a declared percentile")
    if "min_duration_s" in condition and not isinstance(condition["min_duration_s"], (int, float)):
        raise RuleError(f"{path}: min_duration_s must be a number")


def validate_rule(document: dict) -> dict:
    """Validate a pattern document (rule + metadata).  Raises :class:`RuleError`."""
    if not isinstance(document, dict):
        raise RuleError("pattern must be a JSON object")
    for key in ("slug", "title", "sentence_template", "rule", "weight"):
        if key not in document:
            raise RuleError(f"pattern is missing required field {key!r}")
    weight = document["weight"]
    if not isinstance(weight, int) or not 1 <= weight <= 100:
        raise RuleError("weight must be an integer between 1 and 100")
    if not str(document["title"]).strip():
        raise RuleError("title must be non-empty")
    if not str(document["sentence_template"]).strip():
        raise RuleError("sentence_template must be non-empty")
    _validate_condition(document["rule"])
    return document


# ------------------------------------------------------------------ utilities
def _classes_of(condition: dict) -> tuple[str, ...]:
    if condition.get("class"):
        return (condition["class"],)
    return classmap_mod.class_group_members(condition["class_group"])


def _intervals_for(condition: dict, ctx: EvalContext) -> list[dict]:
    wanted = set(_classes_of(condition))
    intervals = [dict(i) for i in ctx.intervals if i.get("class_name") in wanted]
    tolerance = condition.get("gap_tolerance_s")
    if tolerance is not None:
        from .detect import merge_intervals
        intervals = merge_intervals(intervals, float(tolerance))
    return sorted(intervals, key=lambda i: i["t_start"])


def _series_window(ctx: EvalContext, start_s: float, end_s: float, signal: str) -> list[tuple[int, int]]:
    """(second_index, value) pairs covering [start_s, end_s) on the source axis."""
    values = ctx.series.get(signal) or []
    first = max(0, int(start_s))
    last = min(len(values), int(end_s) + 1)
    return [(index, int(values[index])) for index in range(first, last)]


def _mean_motion_milli(ctx: EvalContext, start_s: float, end_s: float) -> int:
    values = [value for _, value in _series_window(ctx, start_s, end_s, "motion")]
    if not values:
        return 0
    return int(round(sum(values) / float(len(values))))


def _runs(pairs: list[tuple[int, int]], predicate) -> list[tuple[float, float, int]]:
    """Consecutive runs where ``predicate(value)`` holds → (start_s, end_s, seconds)."""
    runs: list[tuple[float, float, int]] = []
    run_start: int | None = None
    previous: int | None = None
    for index, value in pairs + [(None, None)]:  # type: ignore[list-item]
        holds = index is not None and predicate(value)
        if holds and run_start is None:
            run_start = index
        elif not holds and run_start is not None:
            end = (previous + 1) if previous is not None else run_start
            runs.append((float(run_start), float(end), end - run_start))
            run_start = None
        previous = index if index is not None else previous
    return runs


def _threshold_for(condition: dict, ctx: EvalContext) -> tuple[int | None, dict]:
    """Resolve a signal_percentile threshold from the stored summary."""
    signal = condition["signal"]
    entry = (ctx.summary or {}).get(signal)
    if not entry or int(entry.get("count", 0)) < MIN_SUMMARY_SAMPLES:
        return None, {"insufficient_samples": True,
                      "samples": int(entry.get("count", 0)) if entry else 0,
                      "required_samples": MIN_SUMMARY_SAMPLES}
    key = f"p{int(condition['percentile'])}"
    value = entry.get(key)
    if value is None:
        return None, {"insufficient_samples": True, "samples": int(entry.get("count", 0)),
                      "required_samples": MIN_SUMMARY_SAMPLES}
    return int(value), {"percentile": int(condition["percentile"]), "threshold_milli": int(value),
                        "summary_samples": int(entry["count"])}


def _produces_spans(condition: dict) -> bool:
    kind = condition.get("type")
    if kind in ("all", "any"):
        return any(_produces_spans(child) for child in condition.get("of", []))
    if kind == "presence":
        return "min_duration_s" in condition
    if kind in ("signal_threshold", "signal_percentile"):
        return "min_duration_s" in condition
    return False


def _intersect(left: list[Span], right: list[Span]) -> list[Span]:
    out: list[Span] = []
    for a in left:
        for b in right:
            start = max(a.t_start, b.t_start)
            end = min(a.t_end, b.t_end)
            if end - start > 0:
                evidence = dict(a.evidence)
                evidence.update({k: v for k, v in b.evidence.items() if k not in evidence})
                # the intersection's own duration, not either input's
                evidence["duration_ms"] = int(round((end - start) * 1000.0))
                out.append(Span(start, end, evidence))
    return sorted(out, key=lambda s: s.t_start)


# ---------------------------------------------------------------- evaluation
def evaluate(rule: dict, ctx: EvalContext) -> EvalResult:
    kind = rule.get("type")
    if kind == "presence":
        return _eval_presence(rule, ctx)
    if kind == "absence":
        return _eval_absence(rule, ctx)
    if kind == "signal_threshold":
        return _eval_signal_threshold(rule, ctx)
    if kind == "signal_percentile":
        return _eval_signal_percentile(rule, ctx)
    if kind == "all":
        return _eval_all(rule, ctx)
    if kind == "any":
        return _eval_any(rule, ctx)
    raise RuleError(f"unknown condition type {kind!r}")


def _eval_presence(rule: dict, ctx: EvalContext) -> EvalResult:
    intervals = _intervals_for(rule, ctx)
    spans: list[Span] = []
    tolerance = rule.get("gap_tolerance_s")
    for interval in intervals:
        duration = float(interval["t_end"]) - float(interval["t_start"])
        if "min_duration_s" in rule and duration < float(rule["min_duration_s"]):
            continue
        motion = _mean_motion_milli(ctx, interval["t_start"], interval["t_end"])
        if "max_motion_energy" in rule:
            ceiling = int(round(float(rule["max_motion_energy"]) * 1000.0))
            if motion > ceiling:
                continue
        evidence = {
            "class": interval.get("class_name"),
            "duration_ms": int(round(duration * 1000.0)),
            "required_duration_ms": int(round(float(rule.get("min_duration_s", 0.0)) * 1000.0)),
            "motion_mean_milli": motion,
            "samples": int(interval.get("samples", 0)),
            "conf_max_milli": int(interval.get("conf_max_milli", 0)),
            "synthetic": bool(interval.get("synthetic", False)),
        }
        if tolerance is not None:
            evidence["gap_tolerance_ms"] = int(round(float(tolerance) * 1000.0))
        elif interval.get("tolerance_used_s") is not None:
            evidence["gap_tolerance_ms"] = int(round(float(interval["tolerance_used_s"]) * 1000.0))
        spans.append(Span(float(interval["t_start"]), float(interval["t_end"]), evidence))
    return EvalResult(spans=spans)


def _eval_absence(rule: dict, ctx: EvalContext) -> EvalResult:
    # Absence produces no spans of its own: it is a constraint on an anchor.
    return EvalResult(spans=[], notes={"absence": True, "classes": list(_classes_of(rule))})


def _eval_signal_threshold(rule: dict, ctx: EvalContext) -> EvalResult:
    signal = rule["signal"]
    minimum = rule.get("min")
    maximum = rule.get("max")
    min_duration = float(rule.get("min_duration_s", 0.0))

    def predicate(value: int) -> bool:
        if minimum is not None and value < int(round(float(minimum) * 1000.0)):
            return False
        if maximum is not None and value > int(round(float(maximum) * 1000.0)):
            return False
        return True

    if signal == "silence_gap":
        spans = []
        for event in ctx.events:
            if event.get("kind") != "silence_gap":
                continue
            start, end = event.get("t_start"), event.get("t_end")
            if start is None or end is None:
                continue
            duration = float(end) - float(start)
            if duration < min_duration:
                continue
            spans.append(Span(float(start), float(end), {
                "signal": "silence_gap",
                "duration_ms": int(round(duration * 1000.0)),
                "gap_ms": int(round(duration * 1000.0)),
                "event_value_milli": int(event.get("value_milli") or 0),
            }))
        return EvalResult(spans=spans)

    window_start = float(ctx.window["start_s"])
    window_end = float(ctx.window["end_s"])
    pairs = _series_window(ctx, window_start, window_end, signal)
    spans = []
    for start, end, seconds in _runs(pairs, predicate):
        if seconds < min_duration:
            continue
        values = [v for _, v in pairs if start <= _ <= end]
        spans.append(Span(start, end, {
            "signal": signal,
            "duration_ms": int(seconds * 1000),
            "value_min_milli": min(values) if values else 0,
            "value_max_milli": max(values) if values else 0,
        }))
    return EvalResult(spans=spans)


def _eval_signal_percentile(rule: dict, ctx: EvalContext) -> EvalResult:
    threshold, notes = _threshold_for(rule, ctx)
    if threshold is None:
        return EvalResult(spans=[], notes=notes)
    op = rule["op"]
    min_duration = float(rule.get("min_duration_s", 0.0))
    signal = rule["signal"]
    window_start = float(ctx.window["start_s"])
    window_end = float(ctx.window["end_s"])
    pairs = _series_window(ctx, window_start, window_end, signal)
    predicate = (lambda value: value >= threshold) if op == "gte" else (lambda value: value <= threshold)
    spans = []
    for start, end, seconds in _runs(pairs, predicate):
        if seconds < min_duration:
            continue
        values = [v for _, v in pairs if start <= _ <= end]
        spans.append(Span(start, end, {
            "signal": signal,
            "duration_ms": int(seconds * 1000),
            f"{signal}_percentile": notes["percentile"],
            f"{signal}_threshold_milli": threshold,
            f"{signal}_value_min_milli": min(values) if values else 0,
            f"{signal}_value_max_milli": max(values) if values else 0,
            f"{signal}_summary_samples": notes["summary_samples"],
            "percentile": notes["percentile"],
            "threshold_milli": threshold,
            "summary_samples": notes["summary_samples"],
        }))
    return EvalResult(spans=spans, notes=notes)


def _eval_all(rule: dict, ctx: EvalContext) -> EvalResult:
    """``all`` — simultaneous conditions, or conditions around one named anchor.

    Two shapes, both explicit:

    * **no anchor** — every span-producing child must hold *simultaneously*, so
      the qualifying span is the intersection of their spans (built-in 2:
      motion above p85 while luma below p10).
    * **one child carries ``"anchor": true``** — that child defines the
      qualifying span and every other child constrains it (built-in 3: an
      animal presence span that contains a silence gap, with no person).
    """
    children = rule.get("of", [])
    results = [evaluate(child, ctx) for child in children]
    notes: dict = {}
    for result in results:
        notes.update(result.notes)
    anchor_indexes = [index for index, child in enumerate(children) if child.get("anchor")]
    if len(anchor_indexes) > 1:
        raise RuleError("an 'all' may name at most one anchor condition")
    if anchor_indexes:
        index = anchor_indexes[0]
        if not _produces_spans(children[index]):
            raise RuleError("the anchor condition must be able to produce a span")
        spans = [Span(span.t_start, span.t_end, dict(span.evidence))
                 for span in results[index].spans]
    else:
        producers = [result.spans for result, child in zip(results, children)
                     if _produces_spans(child)]
        if producers:
            spans = producers[0]
            for other in producers[1:]:
                spans = _intersect(spans, other)
        else:
            spans = [Span(float(ctx.window["start_s"]), float(ctx.window["end_s"]), {})]

    kept: list[Span] = []
    for span in spans:
        merged_evidence = dict(span.evidence)
        satisfied = True
        for index, (child, result) in enumerate(zip(children, results)):
            if index in anchor_indexes:
                continue
            if not anchor_indexes and _produces_spans(child):
                continue  # already intersected
            ok, evidence = _check_constraint(child, span, ctx)
            merged_evidence.update(evidence)
            if not ok:
                satisfied = False
                break
        if satisfied:
            kept.append(Span(span.t_start, span.t_end, merged_evidence))
    return EvalResult(spans=kept, notes=notes)


def _eval_any(rule: dict, ctx: EvalContext) -> EvalResult:
    spans: list[Span] = []
    notes: dict = {}
    for child in rule.get("of", []):
        result = evaluate(child, ctx)
        spans.extend(result.spans)
        notes.update(result.notes)
    return EvalResult(spans=sorted(spans, key=lambda s: s.t_start), notes=notes)


def _check_constraint(condition: dict, span: Span, ctx: EvalContext) -> tuple[bool, dict]:
    kind = condition.get("type")
    if kind == "presence":
        intervals = _intervals_for(condition, ctx)
        overlap = 0.0
        synthetic = False
        for interval in intervals:
            start = max(span.t_start, float(interval["t_start"]))
            end = min(span.t_end, float(interval["t_end"]))
            if end > start:
                overlap += end - start
                synthetic = synthetic or bool(interval.get("synthetic", False))
        required = float(condition.get("min_overlap_s", 0.0))
        evidence = {
            "overlap_class": condition.get("class") or condition.get("class_group"),
            "overlap_ms": int(round(overlap * 1000.0)),
            "required_overlap_ms": int(round(required * 1000.0)),
        }
        if synthetic:
            evidence["synthetic"] = True
        return overlap + 1e-9 >= required, evidence

    if kind == "absence":
        intervals = _intervals_for(condition, ctx)
        overlap = 0.0
        for interval in intervals:
            start = max(span.t_start, float(interval["t_start"]))
            end = min(span.t_end, float(interval["t_end"]))
            if end > start:
                overlap += end - start
        evidence = {
            "absent_class": condition.get("class") or condition.get("class_group"),
            "absent_overlap_ms": int(round(overlap * 1000.0)),
            "non_detection_note": "non-detection is treated as absence for rule purposes",
        }
        return overlap <= 0.0, evidence

    if kind in ("signal_threshold", "signal_percentile"):
        signal = condition["signal"]
        if signal == "silence_gap":
            required = float(condition.get("min_duration_s", 0.0))
            best = 0.0
            overlap = 0.0
            count = 0
            for event in ctx.events:
                if event.get("kind") != "silence_gap":
                    continue
                start, end = event.get("t_start"), event.get("t_end")
                if start is None or end is None:
                    continue
                duration = float(end) - float(start)
                if duration < required:
                    continue
                shared = min(span.t_end, float(end)) - max(span.t_start, float(start))
                if shared <= 0:
                    continue
                count += 1
                best = max(best, duration)
                overlap = max(overlap, shared)
            evidence = {
                "gap_ms": int(round(best * 1000.0)),
                "gap_overlap_ms": int(round(overlap * 1000.0)),
                "silence_gap_events": count,
                "required_hold_ms": int(round(required * 1000.0)),
            }
            return overlap + 1e-9 >= required, evidence
        if kind == "signal_percentile":
            threshold, notes = _threshold_for(condition, ctx)
            if threshold is None:
                return False, {"insufficient_samples": True,
                               "samples": notes.get("samples", 0),
                               "required_samples": MIN_SUMMARY_SAMPLES}
            op = condition["op"]
            predicate = (lambda v: v >= threshold) if op == "gte" else (lambda v: v <= threshold)
            threshold_evidence = {"percentile": notes["percentile"],
                                  f"{signal}_percentile": notes["percentile"],
                                  "threshold_milli": threshold,
                                  f"{signal}_threshold_milli": threshold,
                                  "summary_samples": notes["summary_samples"],
                                  f"{signal}_summary_samples": notes["summary_samples"]}
        else:
            minimum = condition.get("min")
            maximum = condition.get("max")

            def predicate(value: int) -> bool:
                if minimum is not None and value < int(round(float(minimum) * 1000.0)):
                    return False
                if maximum is not None and value > int(round(float(maximum) * 1000.0)):
                    return False
                return True

            threshold_evidence = {
                "min_milli": int(round(float(minimum) * 1000.0)) if minimum is not None else None,
                "max_milli": int(round(float(maximum) * 1000.0)) if maximum is not None else None,
            }
        pairs = _series_window(ctx, span.t_start, span.t_end, signal)
        required = float(condition.get("min_duration_s", 0.0))
        longest = max((seconds for _, _, seconds in _runs(pairs, predicate)), default=0)
        evidence = {"signal": signal, "held_ms": int(longest * 1000),
                    f"{signal}_held_ms": int(longest * 1000),
                    "required_hold_ms": int(round(required * 1000.0)),
                    f"{signal}_required_hold_ms": int(round(required * 1000.0)),
                    **threshold_evidence}
        return longest + 1e-9 >= required, evidence

    return False, {"unsupported_constraint": kind}
