#!/usr/bin/env python3
"""
Convert plan workout objects (as embedded in ICS X-WORKOUT) into FIT workout
steps and optionally attach pace/speed targets. Designed to pair with
final_spec_compliant_fix.export_spec_compliant_fit_workout.

Public API used by ics_to_fit_gui.convert_ics_to_fit:
- workout_to_garmin_steps(workout: dict) -> list[dict]
- compute_obj_miles_minutes(workout: dict) -> tuple[float, float]
- export_fit_workout(name, steps, out_path, estimated_miles=None)

Global tuning knobs (set by ics_to_fit_gui before use):
- TARGETS_ENABLED: bool
- TARGET_MODE: "pace" | "speed"
- TARGET_MARGIN_SEC: int  (± seconds per mile)
- TARGET_INCLUDE_WU_CD: bool  (include warmup/cooldown targets)
- INCLUDE_IMPLICIT_WU_CD: bool (unused here; for mileage/time estimation in generator)
- RACE_PACE_MIN_PER_MILE: float (race-pace minutes per mile)
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import math

import hm_plan_calendar as gen


# --- Target configuration (overridden by caller) ---
TARGETS_ENABLED: bool = False
TARGET_MODE: str = "pace"          # "pace" (sec/km) or "speed" (m/s)
TARGET_MARGIN_SEC: int = 30         # ± seconds per mile
TARGET_INCLUDE_WU_CD: bool = False  # include warmup/cooldown in targets

# Informational flag mirrored from generator (not used directly here)
INCLUDE_IMPLICIT_WU_CD: bool = False

# Athlete target race pace (minutes per mile)
RACE_PACE_MIN_PER_MILE: float = 6.6667


# --- Small helpers ---
def _dur_seconds(dur_str: str) -> Optional[float]:
    """Parse generator duration string (e.g., '10 min', '45 sec') to seconds."""
    m = gen.parse_duration_str(dur_str)
    return float(m) * 60.0 if m is not None else None


def _dist_meters(dist_str: str) -> Optional[float]:
    """Parse generator distance string to meters (prefers the high end of a range)."""
    parsed = gen.parse_distance_str(dist_str)
    if not parsed:
        return None
    (_, _high_mi), unit, (orig_low, orig_high) = parsed
    if unit in ("km", "kilometers"):
        return float(orig_high) * 1000.0
    if unit in ("m", "meters"):
        return float(orig_high)
    # miles default
    return float(orig_high) * 1609.34


def _explicit_pace_min_per_mile(intensity: str) -> Optional[float]:
    """If intensity encodes an explicit pace like '9:30/mi', return min/mi."""
    try:
        p = gen.parse_pace_str(intensity)
        return float(p) if p is not None else None
    except Exception:
        return None


def _intensity_pct(intensity: str) -> float:
    try:
        return float(gen.intensity_to_pct(intensity))
    except Exception:
        return 100.0


def _base_pace_min_per_mile(intensity: str) -> float:
    """Resolve minutes/mile pace for a step from intensity text and race pace."""
    p = _explicit_pace_min_per_mile(intensity or "")
    if p is not None:
        return p
    p = gen.resolve_intensity_pace(intensity or "")
    if p is not None:
        return float(p)
    pct = _intensity_pct(intensity or "")
    return float(gen.pace_at_percentage(RACE_PACE_MIN_PER_MILE, pct))


def _pace_bounds_sec_per_km(min_per_mile: float, margin_sec_per_mile: float) -> Tuple[float, float]:
    """Return (slow_sec_per_km, fast_sec_per_km) around base pace with ±margin (sec/mi)."""
    sec_per_mile = float(min_per_mile) * 60.0
    slow_spm = sec_per_mile + float(margin_sec_per_mile)
    fast_spm = max(1.0, sec_per_mile - float(margin_sec_per_mile))
    # convert to sec/km
    to_km = 1.0 / 1.60934
    return slow_spm * to_km, fast_spm * to_km


def _speed_bounds_mps(min_per_mile: float, margin_sec_per_mile: float) -> Tuple[float, float]:
    """Return (min_speed_mps, max_speed_mps) from base pace ± margin (sec/mi)."""
    sec_per_mile = float(min_per_mile) * 60.0
    slow_spm = sec_per_mile + float(margin_sec_per_mile)
    fast_spm = max(1.0, sec_per_mile - float(margin_sec_per_mile))
    return 1609.34 / slow_spm, 1609.34 / fast_spm


def _is_very_easy(intensity: str) -> bool:
    s = (intensity or "").strip().lower()
    return ("very easy" in s) or ("recovery" in s)

def _is_rest_intensity(intensity: str) -> bool:
    s = (intensity or "").strip().lower()
    return ("rest" in s) or ("walk" in s)


def _should_target(is_recovery: bool, is_warmup: bool, is_cooldown: bool) -> bool:
    if not TARGETS_ENABLED:
        return False
    if is_recovery:
        return False
    if (is_warmup or is_cooldown) and not TARGET_INCLUDE_WU_CD:
        return False
    return True


def _mk_step(
    *,
    step_type: str,
    description: str,
    duration_seconds: Optional[float] = None,
    distance_meters: Optional[float] = None,
    intensity_text: str = "",
    is_recovery: bool = False,
    is_warmup: bool = False,
    is_cooldown: bool = False,
) -> Dict[str, Any]:
    """Create a step dict understood by the FIT exporter."""
    st: Dict[str, Any] = {
        "type": step_type,
        "description": description,
    }
    # Duration mapping
    if duration_seconds is not None:
        st["endCondition"] = "TIME"
        st["endConditionValue"] = float(duration_seconds)
    elif distance_meters is not None:
        st["endCondition"] = "DISTANCE"
        st["endConditionValue"] = float(distance_meters)
    else:
        st["endCondition"] = "OPEN"

    # Targets mapping
    if _should_target(is_recovery, is_warmup, is_cooldown) and not gen.is_effort_only_intensity(intensity_text):
        base_min_per_mile = _base_pace_min_per_mile(intensity_text)
        if TARGET_MODE == "speed":
            v_low, v_high = _speed_bounds_mps(base_min_per_mile, TARGET_MARGIN_SEC)
            st["targetType"] = "SPEED"
            st["targetValueLow"] = float(v_low)
            st["targetValueHigh"] = float(v_high)
        else:
            # pace mode uses sec/km for exporter; it will convert to m/s
            p_low, p_high = _pace_bounds_sec_per_km(base_min_per_mile, TARGET_MARGIN_SEC)
            st["targetType"] = "PACE"
            st["targetValueLow"] = float(p_low)   # slow (larger sec)
            st["targetValueHigh"] = float(p_high) # fast (smaller sec)

    return st


def _flatten_simple(
    *,
    ds: Optional[str],
    intensity: str,
    reps: int,
    workout_type: str,
    is_warmup: bool = False,
    is_cooldown: bool = False,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    duration_seconds = _dur_seconds(ds or "") if ds else None
    distance_meters = _dist_meters(ds or "") if (ds and duration_seconds is None) else None
    desc_core = f"{ds or ''} {intensity}".strip()
    for _ in range(max(1, int(reps or 1))):
        out.append(
            _mk_step(
                step_type=workout_type,
                description=desc_core,
                duration_seconds=duration_seconds,
                distance_meters=distance_meters,
                intensity_text=intensity,
                is_recovery=False,
                is_warmup=is_warmup,
                is_cooldown=is_cooldown,
            )
        )
    return out


def _flatten_with_recovery(
    *,
    ds: Optional[str],
    intensity: str,
    reps: int,
    rec: Optional[Dict[str, Any]],
    workout_type: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for _ in range(max(1, int(reps or 1))):
        out.extend(
            _flatten_simple(ds=ds, intensity=intensity, reps=1, workout_type=workout_type)
        )
        if rec and (rec.get("duration") or rec.get("distance")):
            rds = rec.get("duration") or rec.get("distance")
            rkind = (rec.get("type") or "recovery").strip().lower()
            duration_seconds = _dur_seconds(rds)
            distance_meters = _dist_meters(rds) if duration_seconds is None else None
            out.append(
                _mk_step(
                    step_type=f"recovery {rkind}",
                    description=f"{rds} {rkind}",
                    duration_seconds=duration_seconds,
                    distance_meters=distance_meters,
                    intensity_text="very easy",
                    is_recovery=True,
                )
            )
    return out


def _flatten_segments(segments: List[Dict[str, Any]], workout_type: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    n = len(segments)
    def _is_break_set(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("repetitions") is not None:
            return False
        if "sequence" in item:
            return False
        return bool(item.get("duration") or item.get("distance"))

    for idx, seg in enumerate(segments):
        # Identify warmup/cooldown heuristically: 'Very easy' at edges with duration or distance
        intensity = (seg.get("intensity") or "").strip()
        is_ve = _is_very_easy(intensity)
        is_wu = is_ve and idx == 0 and (seg.get("duration") is not None or seg.get("distance") is not None)
        is_cd = is_ve and idx == n - 1 and (seg.get("duration") is not None or seg.get("distance") is not None)

        if "sets" in seg and isinstance(seg["sets"], list):
            for s in seg["sets"]:
                if _is_break_set(s):
                    ds = s.get("distance") or s.get("duration")
                    intensity = (s.get("intensity") or "").strip()
                    duration_seconds = _dur_seconds(ds) if ds else None
                    distance_meters = _dist_meters(ds) if (ds and duration_seconds is None) else None
                    out.append(
                        _mk_step(
                            step_type="recovery",
                            description=f"{ds} {intensity}".strip(),
                            duration_seconds=duration_seconds,
                            distance_meters=distance_meters,
                            intensity_text="very easy",
                            is_recovery=True,
                        )
                    )
                    continue
                reps = int(s.get("repetitions", 1) or 1)
                if "sequence" in s and isinstance(s["sequence"], list):
                    for _ in range(max(1, reps)):
                        for step in s["sequence"]:
                            ds = step.get("distance") or step.get("duration")
                            intensity = (step.get("intensity") or "").strip()
                            rec = step.get("recovery") if isinstance(step.get("recovery"), dict) else None
                            step_reps = int(step.get("repetitions", 1) or 1)
                            if rec:
                                out.extend(
                                    _flatten_with_recovery(ds=ds, intensity=intensity, reps=step_reps, rec=rec, workout_type=workout_type)
                                )
                            else:
                                out.extend(
                                    _flatten_simple(ds=ds, intensity=intensity, reps=step_reps, workout_type=workout_type)
                                )
                else:
                    ds = s.get("distance") or s.get("duration")
                    intensity = (s.get("intensity") or "").strip()
                    rec = s.get("recovery") if isinstance(s.get("recovery"), dict) else None
                    if rec:
                        out.extend(
                            _flatten_with_recovery(ds=ds, intensity=intensity, reps=reps, rec=rec, workout_type=workout_type)
                        )
                    else:
                        out.extend(
                            _flatten_simple(ds=ds, intensity=intensity, reps=reps, workout_type=workout_type)
                        )
        else:
            reps = int(seg.get("repetitions", 1) or 1)
            ds = seg.get("distance") or seg.get("duration")
            intensity = (seg.get("intensity") or "").strip()
            rec = seg.get("recovery") if isinstance(seg.get("recovery"), dict) else None
            if rec:
                out.extend(
                    _flatten_with_recovery(ds=ds, intensity=intensity, reps=reps, rec=rec, workout_type=workout_type)
                )
            else:
                if is_wu or is_cd:
                    out.extend(
                        _flatten_simple(
                            ds=ds,
                            intensity=intensity,
                            reps=reps,
                            workout_type=("warmup" if is_wu else "cooldown"),
                            is_warmup=is_wu,
                            is_cooldown=is_cd,
                        )
                    )
                elif _is_rest_intensity(intensity):
                    duration_seconds = _dur_seconds(ds or "") if ds else None
                    distance_meters = _dist_meters(ds or "") if (ds and duration_seconds is None) else None
                    for _ in range(max(1, reps)):
                        out.append(
                            _mk_step(
                                step_type="recovery",
                                description=f"{ds or ''} {intensity}".strip(),
                                duration_seconds=duration_seconds,
                                distance_meters=distance_meters,
                                intensity_text="very easy",
                                is_recovery=True,
                            )
                        )
                else:
                    out.extend(
                        _flatten_simple(ds=ds, intensity=intensity, reps=reps, workout_type=workout_type)
                    )
    return out


def _has_explicit_wu_cd(obj: Any) -> bool:
    """Detect explicit WU/CD segments labeled 'Very easy' at the edges."""
    if isinstance(obj, dict):
        if ("am" in obj) or ("pm" in obj):
            return False
        segments = obj.get("segments")
        if isinstance(segments, list) and segments:
            first = segments[0]
            last = segments[-1]

            def _is_ve_step(seg: Any) -> bool:
                if not isinstance(seg, dict):
                    return False
                intensity = (seg.get("intensity") or "").strip().lower()
                if "very easy" not in intensity and "recovery" not in intensity:
                    return False
                return seg.get("duration") is not None or seg.get("distance") is not None

            return _is_ve_step(first) or _is_ve_step(last)
    return False


def _inject_implicit_wu_cd(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add warmup and cooldown steps when implicit WU/CD is enabled."""
    if not INCLUDE_IMPLICIT_WU_CD:
        return steps
    try:
        total_mi, total_min = gen.get_implicit_wu_cd_miles_time()
    except Exception:
        total_mi, total_min = (1.0, 9.25)
    if total_mi <= 0 and total_min <= 0:
        return steps

    use_distance = getattr(gen, "implicit_wu_cd_distance_miles", None) is not None
    if use_distance:
        half_mi = max(0.0, float(total_mi) / 2.0)
        wu_meters = half_mi * 1609.34
        warmup = _mk_step(
            step_type="warmup",
            description=f"{half_mi:.2f} mi very easy",
            distance_meters=wu_meters,
            intensity_text="very easy",
            is_warmup=True,
            is_cooldown=False,
        )
        cooldown = _mk_step(
            step_type="cooldown",
            description=f"{half_mi:.2f} mi very easy",
            distance_meters=wu_meters,
            intensity_text="very easy",
            is_warmup=False,
            is_cooldown=True,
        )
    else:
        half_min = max(0.0, float(total_min) / 2.0)
        warmup = _mk_step(
            step_type="warmup",
            description=f"{half_min:.0f} min very easy",
            duration_seconds=half_min * 60.0,
            intensity_text="very easy",
            is_warmup=True,
            is_cooldown=False,
        )
        cooldown = _mk_step(
            step_type="cooldown",
            description=f"{half_min:.0f} min very easy",
            duration_seconds=half_min * 60.0,
            intensity_text="very easy",
            is_warmup=False,
            is_cooldown=True,
        )
    return [warmup] + (steps or []) + [cooldown]


def _flatten_obj(obj: Any, workout_type_hint: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        # AM/PM container
        if ("am" in obj) or ("pm" in obj):
            if isinstance(obj.get("am"), (dict, list)):
                out.extend(_flatten_obj(obj["am"], workout_type_hint or (obj.get("am", {}).get("type") or "")))
            if isinstance(obj.get("pm"), (dict, list)):
                out.extend(_flatten_obj(obj["pm"], workout_type_hint or (obj.get("pm", {}).get("type") or "")))
            return out

        wt = (obj.get("type") or workout_type_hint or "").strip().lower() or "run"
        # Segments
        if "segments" in obj and isinstance(obj["segments"], list):
            out.extend(_flatten_segments(obj["segments"], wt))
            return out
        # Sets (top-level)
        if "sets" in obj and isinstance(obj["sets"], list):
            out.extend(_flatten_segments([{"sets": obj["sets"]}], wt))
            return out
        # Simple entry
        ds = obj.get("distance") or obj.get("duration")
        reps = int(obj.get("repetitions", 1) or 1)
        intensity = (obj.get("intensity") or "").strip()
        out.extend(_flatten_simple(ds=ds, intensity=intensity, reps=reps, workout_type=wt))
        return out
    elif isinstance(obj, list):
        for it in obj:
            out.extend(_flatten_obj(it, workout_type_hint))
        return out
    # Unknown
    return out


# --- Repeat step injection ---
def _inject_repeat_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse consecutive identical step sequences into a single block + repeat marker.

    Garmin watches surface lap/rep counters only when a dedicated repeat step is
    present in the FIT workout. The generator historically expanded each
    repetition into standalone steps, so this pass detects contiguous repeated
    patterns (e.g., interval + recovery pairs) and replaces them with a single
    instance of the pattern followed by a synthetic repeat marker. The repeat
    metadata is then translated into a FIT repeat step by the exporter.
    """

    n = len(steps)
    if n <= 1:
        return steps[:]

    out: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        remaining = n - i
        max_block = min(remaining // 2, 12)
        best_block_len = 0
        best_reps = 1

        for block_len in range(1, max_block + 1):
            block = steps[i:i + block_len]
            reps = 1
            while i + reps * block_len + block_len <= n and steps[i + reps * block_len:i + (reps + 1) * block_len] == block:
                reps += 1
            if reps > 1:
                # Prefer the block that covers the most total steps.
                if block_len * reps > best_block_len * best_reps:
                    best_block_len = block_len
                    best_reps = reps

        if best_reps > 1 and best_block_len > 0:
            block = steps[i:i + best_block_len]
            out.extend(block)
            out.append({
                "type": "repeat",
                "repeat": {
                    "block_len": best_block_len,
                    "count": best_reps,
                },
            })
            i += best_block_len * best_reps
        else:
            out.append(steps[i])
            i += 1

    return out


# --- Public API ---
def workout_to_garmin_steps(workout: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a plan workout object into a list of FIT step dicts.

    Each step dict includes:
    - 'type' and 'description' strings (for notes and intensity mapping)
    - 'endCondition' in {TIME, DISTANCE, OPEN}
    - 'endConditionValue' (seconds for TIME, meters for DISTANCE)
    - Optional targets when enabled: 'targetType' in {PACE, SPEED}
      and 'targetValueLow'/'targetValueHigh'
    """
    try:
        steps = _flatten_obj(workout)
        if INCLUDE_IMPLICIT_WU_CD and gen.is_wu_cd_workout(workout) and not _has_explicit_wu_cd(workout):
            if isinstance(workout, dict) and not (("am" in workout) or ("pm" in workout)):
                steps = _inject_implicit_wu_cd(steps)
        steps = _inject_repeat_steps(steps)
        return steps
    except Exception:
        return []


def compute_obj_miles_minutes(workout: Dict[str, Any]) -> Tuple[float, float]:
    """Return (miles, minutes) estimates for the workout object."""
    try:
        mi = float(gen.compute_day_mileage(workout))
    except Exception:
        mi = 0.0
    try:
        mins = float(gen.compute_day_estimated_time_mins(workout))
    except Exception:
        mins = 0.0
    return mi, mins


def export_fit_workout(workout_name: str, steps: List[Dict[str, Any]], out_path: str, estimated_miles: Optional[float] = None) -> None:
    """Fallback exporter: delegate to the spec-compliant exporter when available.

    If fit_tool is not installed, this raises a clear error. ics_to_fit_gui will
    surface a helpful message instructing installation.
    """
    try:
        from final_spec_compliant_fix import export_spec_compliant_fit_workout
    except Exception:
        raise RuntimeError("fit_tool not installed. Install with: pip install fit_tool")
    export_spec_compliant_fit_workout(workout_name, steps, out_path, estimated_miles=estimated_miles)
