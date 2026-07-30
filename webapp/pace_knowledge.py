"""Running pace vocabulary, athlete pace profiles, and deterministic inference.

The LLM is responsible for understanding workout structure.  This module is
responsible for pace math so a model cannot accidentally turn a familiar coach
term into the wrong watch target.  User-entered paces always win; missing paces
are inferred from the strongest available anchors.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Optional


RIEGEL_EXPONENT = 1.06

RACE_DISTANCES_MI: Dict[str, float] = {
    "800m": 0.8 / 1.609344,
    "1500m": 1.5 / 1.609344,
    "mile": 1.0,
    "2_mile": 2.0,
    "3k": 3.0 / 1.609344,
    "5k": 5.0 / 1.609344,
    "8k": 8.0 / 1.609344,
    "10k": 10.0 / 1.609344,
    "15k": 15.0 / 1.609344,
    "10_mile": 10.0,
    "half_marathon": 13.1094,
    "25k": 25.0 / 1.609344,
    "30k": 30.0 / 1.609344,
    "marathon": 26.2188,
    "50k": 50.0 / 1.609344,
}

# Target speed divided by equivalent 10K speed.  These are deliberately
# central estimates rather than physiological boundaries.  Direct athlete
# anchors and explicit workout paces always take precedence.
TRAINING_SPEED_FACTORS_10K: Dict[str, float] = {
    "recovery": 0.70,
    "easy": 0.75,
    "general_aerobic": 0.80,
    "steady": 0.87,
    "marathon": 0.90,
    "subthreshold": 0.94,
    "half_marathon": 0.955,
    "threshold": 0.97,
    "critical_velocity": 0.995,
    "10k": 1.00,
    "8k": 1.02,
    "5k": 1.05,
    "vo2max": 1.075,
    "3k": 1.10,
    "repetition": 1.15,
    "mile": 1.15,
}

PACE_INPUTS = (
    ("easy", "Easy / conversational"),
    ("marathon", "Marathon"),
    ("half_marathon", "Half marathon"),
    ("threshold", "Lactate threshold / T"),
    ("10k", "10K"),
    ("5k", "5K"),
    ("3k", "3K"),
    ("mile", "Mile / repetition"),
)

_KEY_ALIASES = {
    "recovery": "recovery",
    "recovery_pace": "recovery",
    "regeneration": "recovery",
    "regenerative": "recovery",
    "easy": "easy",
    "e": "easy",
    "easy_pace": "easy",
    "conversational": "easy",
    "general_aerobic": "general_aerobic",
    "general_aerobic_pace": "general_aerobic",
    "ga": "general_aerobic",
    "steady": "steady",
    "steady_pace": "steady",
    "lt1": "steady",
    "aerobic_threshold": "steady",
    "marathon": "marathon",
    "marathon_pace": "marathon",
    "mp": "marathon",
    "half": "half_marathon",
    "half_marathon": "half_marathon",
    "half_marathon_pace": "half_marathon",
    "hm": "half_marathon",
    "hmp": "half_marathon",
    "subthreshold": "subthreshold",
    "sub_threshold": "subthreshold",
    "threshold": "threshold",
    "threshold_pace": "threshold",
    "lactate_threshold": "threshold",
    "lt": "threshold",
    "lt2": "threshold",
    "t": "threshold",
    "t_pace": "threshold",
    "critical_velocity": "critical_velocity",
    "critical_speed": "critical_velocity",
    "cv": "critical_velocity",
    "10k": "10k",
    "10_k": "10k",
    "10km": "10k",
    "8k": "8k",
    "8km": "8k",
    "5k": "5k",
    "5_k": "5k",
    "5km": "5k",
    "vo2max": "vo2max",
    "vo2_max": "vo2max",
    "interval": "vo2max",
    "i": "vo2max",
    "i_pace": "vo2max",
    "vvo2max": "vo2max",
    "mas": "vo2max",
    "3k": "3k",
    "3_k": "3k",
    "3km": "3k",
    "mile": "mile",
    "1500m": "1500m",
    "1500_m": "1500m",
    "800m": "800m",
    "800_m": "800m",
    "2_mile": "2_mile",
    "two_mile": "2_mile",
    "mile_pace": "mile",
    "repetition": "repetition",
    "repetition_pace": "repetition",
    "r": "repetition",
    "r_pace": "repetition",
    "15k": "15k",
    "15km": "15k",
    "10_mile": "10_mile",
    "25k": "25k",
    "30k": "30k",
    "50k": "50k",
}

_EXPLICIT_PACE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*:\s*([0-5]\d)\s*"
    r"(?:min(?:ute)?s?\s*)?(?:/|per\s+)?\s*(mi(?:le)?|km|kilomet(?:er|re))s?\b",
    re.IGNORECASE,
)


def normalize_race_distance(value: Any) -> Optional[str]:
    s = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    if "half" in s or "13.1" in s or "21.1" in s or "21.097" in s:
        return "half_marathon"
    if "marathon" in s or "26.2" in s or "42.1" in s or "42.195" in s:
        return "marathon"
    if "10 mile" in s or "10mi" in s:
        return "10_mile"
    if "50k" in s or "50 km" in s:
        return "50k"
    if "30k" in s or "30 km" in s:
        return "30k"
    if "25k" in s or "25 km" in s:
        return "25k"
    if "15k" in s or "15 km" in s:
        return "15k"
    if "10k" in s or "10 km" in s or "10000" in s or "10,000" in s:
        return "10k"
    if "8k" in s or "8 km" in s:
        return "8k"
    if "5k" in s or "5 km" in s or "5000" in s or "5,000" in s:
        return "5k"
    if "3k" in s or "3 km" in s or "3000" in s or "3,000" in s:
        return "3k"
    if "2 mile" in s or "2mi" in s or "3200" in s:
        return "2_mile"
    if "1500" in s:
        return "1500m"
    if "800" in s:
        return "800m"
    if s in {"mile", "1 mile", "1mi", "1600", "1600m"}:
        return "mile"
    return None


def normalize_anchor_key(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    raw = raw.replace("-", "_").replace("/", "_").replace(" ", "_")
    raw = re.sub(r"_+", "_", raw).strip("_")
    return _KEY_ALIASES.get(raw) or normalize_race_distance(raw)


def parse_pace(value: Any) -> Optional[float]:
    """Parse a pace and return minutes per mile.

    Supported examples: 6:30, 6:30/mi, 4:02/km, and decimal min/mi.  A bare
    mm:ss value is treated as min/mi because that is the app's display unit.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        pace = float(value)
        return pace if math.isfinite(pace) and pace > 0 else None
    s = str(value).strip().lower()
    if not s:
        return None
    m = _EXPLICIT_PACE_RE.search(s)
    if m:
        pace = int(m.group(1)) + int(m.group(2)) / 60.0
        return pace * 1.609344 if m.group(3).lower().startswith("k") else pace
    bare = re.fullmatch(r"(\d{1,2})\s*:\s*([0-5]?\d)", s)
    if bare:
        return int(bare.group(1)) + int(bare.group(2)) / 60.0
    pace_word = re.search(r"(?<!\d)(\d{1,2})\s*:\s*([0-5]\d)\s*(?:pace|min(?:ute)?s? per mile)\b", s)
    if pace_word:
        return int(pace_word.group(1)) + int(pace_word.group(2)) / 60.0
    decimal = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if decimal:
        pace = float(s)
        return pace if pace > 0 else None
    return None


def format_pace(pace_min_per_mile: float) -> str:
    total_seconds = max(1, int(round(float(pace_min_per_mile) * 60.0)))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}/mi"


def normalize_pace_profile(
    paces: Any = None,
    *,
    reference_pace: Any = None,
    race_distance: Any = None,
    easy_pace: Any = None,
) -> Dict[str, float]:
    """Return a clean pace-anchor mapping while retaining every valid anchor."""
    out: Dict[str, float] = {}
    items: Iterable[tuple[Any, Any]] = ()
    if isinstance(paces, Mapping):
        items = paces.items()
    elif isinstance(paces, list):
        items = (
            (item.get("type") or item.get("key") or item.get("name"), item.get("pace") or item.get("value"))
            for item in paces
            if isinstance(item, Mapping)
        )
    for raw_key, raw_value in items:
        key = normalize_anchor_key(raw_key)
        pace = parse_pace(raw_value)
        if key and pace:
            out[key] = pace

    goal_key = normalize_race_distance(race_distance) or "half_marathon"
    legacy = parse_pace(reference_pace)
    if legacy and goal_key not in out:
        out[goal_key] = legacy
    easy = parse_pace(easy_pace)
    if easy and "easy" not in out:
        out["easy"] = easy
    return out


def _pace_at_distance(pace: float, from_key: str, to_key: str) -> float:
    from_miles = RACE_DISTANCES_MI[from_key]
    to_miles = RACE_DISTANCES_MI[to_key]
    return float(pace) * ((to_miles / from_miles) ** (RIEGEL_EXPONENT - 1.0))


def _median_10k_equivalent(profile: Mapping[str, float], *, allow_aerobic: bool = True) -> Optional[float]:
    strong: list[float] = []
    aerobic: list[float] = []
    for key, raw_pace in profile.items():
        pace = parse_pace(raw_pace)
        if not pace:
            continue
        if key in RACE_DISTANCES_MI:
            strong.append(_pace_at_distance(pace, key, "10k"))
        elif key in {"threshold", "subthreshold", "critical_velocity", "vo2max", "repetition"}:
            factor = TRAINING_SPEED_FACTORS_10K[key]
            strong.append(pace * factor)
        elif key in TRAINING_SPEED_FACTORS_10K:
            aerobic.append(pace * TRAINING_SPEED_FACTORS_10K[key])
    candidates = strong or (aerobic if allow_aerobic else [])
    return float(median(candidates)) if candidates else None


def estimate_anchor_pace(key: str, profile: Mapping[str, float]) -> Optional[float]:
    canonical = normalize_anchor_key(key)
    if not canonical:
        return None
    direct = parse_pace(profile.get(canonical))
    if direct:
        return direct
    ten_k = _median_10k_equivalent(profile)
    if not ten_k:
        return None
    if canonical in RACE_DISTANCES_MI:
        return _pace_at_distance(ten_k, "10k", canonical)
    factor = TRAINING_SPEED_FACTORS_10K.get(canonical)
    return (ten_k / factor) if factor else None


def _goal_race_pace(profile: Mapping[str, float], race_distance: Any) -> Optional[float]:
    goal = normalize_race_distance(race_distance) or "half_marathon"
    return estimate_anchor_pace(goal, profile)


def canonical_intensity_key(intensity: Any) -> Optional[str]:
    """Map coach vocabulary to a pace concept, ordered from specific to broad."""
    s = str(intensity or "").strip().lower()
    if not s:
        return None
    s = s.replace("–", "-").replace("—", "-")

    # Race/event paces before generic words such as "tempo" or "interval".
    race_patterns = (
        ("800m", r"\b800\s*m\s*(?:race\s*)?(?:pace|effort)\b"),
        ("1500m", r"\b1500\s*m\s*(?:race\s*)?(?:pace|effort)\b"),
        ("2_mile", r"\b(?:2|two)\s*mile\s*(?:race\s*)?(?:pace|effort)\b"),
        ("10_mile", r"\b10\s*mile\s*(?:race\s*)?(?:pace|effort)\b"),
        ("15k", r"\b(?:15\s*k|15\s*km)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("25k", r"\b(?:25\s*k|25\s*km)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("30k", r"\b(?:30\s*k|30\s*km)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("50k", r"\b(?:50\s*k|50\s*km)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("mile", r"\b(?:mile|1600\s*m)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("3k", r"\b(?:3\s*k|3\s*km|3000\s*m)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("5k", r"\b(?:5\s*k|5\s*km|5000\s*m)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("8k", r"\b(?:8\s*k|8\s*km)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("10k", r"\b(?:10\s*k|10\s*km|10000\s*m)\s*(?:race\s*)?(?:pace|effort)\b"),
        ("half_marathon", r"\b(?:half(?:\s*marathon)?|hm|hmp)\s*(?:race\s*)?(?:pace|effort)?\b"),
        ("marathon", r"\b(?:goal\s*)?(?:marathon|mp|m pace)\s*(?:race\s*)?(?:pace|effort)?\b"),
    )
    for key, pattern in race_patterns:
        if re.search(pattern, s):
            return key

    if re.search(r"\b(?:r pace|repetition pace|rep pace)\b", s):
        return "repetition"
    if re.search(r"\b(?:i pace|interval pace|vo2\s*max(?: pace)?|vvo2\s*max|vvo2max|maximal aerobic speed|mas)\b", s):
        return "vo2max"
    if re.search(r"\b(?:critical velocity|critical speed|cv pace|cv)\b", s):
        return "critical_velocity"
    if re.search(r"\b(?:lt1|aerobic threshold|aet|steady|upper aerobic|moderate)\b", s):
        return "steady"
    if re.search(r"\b(?:sub[ -]?threshold|norwegian threshold|double threshold)\b", s):
        return "subthreshold"
    if re.search(
        r"\b(?:lactate threshold|anaerobic threshold|lt2|lt|threshold|t pace|tempo(?: pace)?|"
        r"cruise intervals?|one[ -]?hour (?:race )?pace|mlss|maximum lactate steady state|comfortably hard)\b",
        s,
    ):
        return "threshold"
    if re.search(r"\b(?:general aerobic|ga pace|aerobic endurance|easy to moderate|medium[ -]?long|long run pace|endurance(?: pace)?)\b", s):
        return "general_aerobic"
    if re.search(r"\b(?:recovery|regeneration|regenerative|shakeout|very easy)\b", s):
        return "recovery"
    if re.search(r"\b(?:easy|e pace|conversational|zone 2|z2|long slow distance|lsd|relaxed running)\b", s):
        return "easy"
    return None


def is_effort_only_intensity(intensity: Any) -> bool:
    """True for cues where a fixed flat-ground pace target would be misleading."""
    s = str(intensity or "").strip().lower()
    return bool(re.search(
        r"\b(?:stride|strides|sprint|all[ -]?out|hill|uphill|downhill|surge|fast relaxed|form fast)\b",
        s,
    )) and parse_pace(s) is None


def _labeled_base_key(label: str, race_distance: Any) -> Optional[str]:
    s = label.strip().lower()
    if "race" in s and not re.search(r"(?:5|8|10|half|marathon|mile)", s):
        return normalize_race_distance(race_distance) or "half_marathon"
    return canonical_intensity_key(label) or normalize_anchor_key(label)


def resolve_intensity_pace(
    intensity: Any,
    profile: Mapping[str, float],
    race_distance: Any = None,
) -> Optional[float]:
    """Resolve an intensity to min/mi, honoring explicit and entered paces first."""
    text = str(intensity or "").strip()
    explicit = parse_pace(text)
    if explicit:
        return explicit
    if not text or is_effort_only_intensity(text):
        return None

    normalized = normalize_pace_profile(profile, race_distance=race_distance)
    percent = re.search(
        r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?\s*%\s*(?:of\s*)?(.+?)(?:\)|$)",
        text,
        re.IGNORECASE,
    )
    if percent:
        low = float(percent.group(1))
        high = float(percent.group(2) or low)
        pct = (low + high) / 2.0
        base_key = _labeled_base_key(percent.group(3), race_distance)
        base = estimate_anchor_pace(base_key, normalized) if base_key else _goal_race_pace(normalized, race_distance)
        if base and pct > 0:
            return base * 100.0 / pct

    s = text.lower()
    goal = _goal_race_pace(normalized, race_distance)
    if "hanson" in s:
        marathon = estimate_anchor_pace("marathon", normalized)
        if marathon and "tempo" in s:
            return marathon
        if marathon and "strength" in s:
            return max(0.1, marathon - (10.0 / 60.0))
    # Canova vocabulary is event-relative.  "Special" alone deliberately has
    # no fixed pace because it may mean either side of race pace.
    if re.search(r"\b(?:specific extensive|extensive specific)\b", s) and goal:
        return goal / 0.98
    if re.search(r"\b(?:specific intensive|intensive specific)\b", s) and goal:
        return goal / 1.02
    if re.search(r"\b(?:specific endurance|specific pace|race specific)\b", s) and goal:
        return goal
    if re.search(r"\b(?:fundamental|fundamental endurance)\b", s) and goal:
        return goal / 0.85
    if re.search(r"\b(?:regeneration|regenerative)\b", s) and goal:
        return normalized.get("recovery") or goal / 0.75
    if re.search(r"\bextensive aerobic endurance\b", s) and goal:
        return goal / 0.90
    if re.search(r"\bintensive aerobic endurance\b", s) and goal:
        return goal / 0.95
    if re.search(r"\baerobic power\b", s) and goal:
        return goal / 1.03
    if re.search(r"\b(?:special endurance|special speed|special pace)\b", s):
        return None

    key = canonical_intensity_key(text)
    if key:
        return estimate_anchor_pace(key, normalized)
    if re.fullmatch(r"(?:race pace|goal pace|specific)", s):
        return goal
    return None


def _annotate_intensity(value: Any, profile: Mapping[str, float], race_distance: Any) -> Any:
    if not isinstance(value, str) or parse_pace(value):
        return value
    pace = resolve_intensity_pace(value, profile, race_distance)
    if not pace:
        return value
    return f"{value.strip()} ({format_pace(pace)})"


def resolve_plan_paces(plan: Dict[str, Any], profile: Mapping[str, float], race_distance: Any = None) -> Dict[str, Any]:
    """Copy a model-produced plan and append the resolved pace to intensities."""
    result = deepcopy(plan)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "intensity" in node:
                node["intensity"] = _annotate_intensity(node.get("intensity"), profile, race_distance)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def prompt_pace_context(profile: Mapping[str, float], race_distance: Any = None) -> str:
    normalized = normalize_pace_profile(profile, race_distance=race_distance)
    if not normalized:
        athlete = "No athlete pace anchors were supplied. Preserve effort terms; do not invent a precise pace."
    else:
        labels = dict(PACE_INPUTS)
        entered = ", ".join(
            f"{labels.get(key, key.replace('_', ' ').title())}: {format_pace(value)}"
            for key, value in normalized.items()
        )
        inferred_keys = ("recovery", "easy", "general_aerobic", "steady", "marathon", "half_marathon", "threshold", "critical_velocity", "10k", "5k", "vo2max", "3k", "mile")
        inferred = ", ".join(
            f"{key.replace('_', ' ')} {format_pace(pace)}"
            for key in inferred_keys
            if key not in normalized and (pace := estimate_anchor_pace(key, normalized)) is not None
        )
        athlete = f"Entered athlete anchors (authoritative): {entered}."
        if inferred:
            athlete += f" Useful estimates for missing terms: {inferred}."

    return f"""
PACE INTERPRETATION POLICY
{athlete}
- Precedence: an explicit pace in the workout text > an entered athlete anchor > a named race-pace equivalent > a terminology estimate. Never discard or overwrite entered anchors.
- Preserve the coach's intended intensity label in `intensity`; the application deterministically attaches the final numeric pace after parsing.
- Race equivalence uses a conservative Riegel estimate. A race result describes current fitness; a goal pace is not automatically current fitness unless the user says it is.
- Lactate threshold / LT / LT2 / Daniels T / cruise intervals / comfortably hard / one-hour race effort mean approximately one-hour race pace. For the common runner whose 10K takes less than an hour, LT is slightly SLOWER than 10K pace, not equal to or faster than it. Continuous tempo usually means this intensity; "tempo" is otherwise ambiguous and should use duration/context.
- Sub-threshold, controlled threshold, Norwegian threshold, and double-threshold work are normally a little slower than LT2. LT1 / aerobic threshold / AeT is substantially slower and belongs near steady/upper-aerobic effort. Do not confuse LT1 with LT2.
- Daniels: E=easy, M=marathon, T=threshold, I=VO2max/roughly 3K-5K effort, R=repetition/roughly mile effort with full recovery. Single-letter meanings apply only when the prompt clearly uses Daniels notation.
- Pfitzinger: recovery=very easy; general aerobic=easy-to-steady aerobic; endurance/medium-long/long runs normally progress through the aerobic range; lactate threshold=roughly 15K-to-half-marathon/one-hour effort; VO2max reps=roughly 3K-5K effort; speed work=strides/repetitions; marathon-pace work=MP. A workout's total mileage includes warm-up and cool-down unless wording says otherwise.
- Canova: regeneration is very easy and event-relative; fundamental is aerobic support; specific is at goal-event pace; specific extensive/intensive are just slower/faster than goal-event pace; special training supports specific work and may be on either side of race pace. Never turn the bare word "special" or "special block" into one fixed pace—use stated percentages, event pace, segment details, or effort.
- Hansons: "tempo" in a marathon plan normally means goal marathon pace, while "strength" reps are commonly about 10 seconds/mile FASTER than goal marathon pace in that system. Do not apply those meanings unless Hansons or marathon-plan context is clear.
- Tinman/Schwartz: CV means critical velocity (roughly current 30-40 minute race effort, around 10K for many runners), not LT; Tinman tempo/easy tempo is slower than threshold. McMillan steady-state is between easy and threshold, tempo is near LT, and speed/sprint labels depend on rep duration. Lydiard aerobic, three-quarter effort, time trial, hill, and sharpening language is effort/context based rather than one universal pace.
- Common aliases: recovery jog/shakeout/regeneration; easy/conversational/E; general aerobic/GA/endurance; steady/moderate/upper aerobic/LT1; MP; HMP; LT/T/tempo/cruise; CV/critical speed; 10-mile/15K/10K/8K/5K/3K/mile effort; VO2max/vVO2max/MAS/I; repetition/R/mile effort.
- Numbered zones are not universal: running Z2 often means easy aerobic, but watch, heart-rate, five-zone, three-zone, and Norwegian/lactate systems use different boundaries. Preserve a zone label unless its system or a matching entered pace is clear. Likewise, RPE and talk-test cues should remain effort cues.
- Generic "fast", "hard", fartlek, hills, strides, and sprints are duration-, terrain-, and recovery-dependent. Infer structure from context but do not invent a rigid flat-ground pace for hills, strides, sprints, or all-out work. When duration clarifies generic reps: ~6-15 min usually threshold/CV, ~2-5 min usually 10K-to-5K/VO2, ~45-120 sec usually 5K-to-3K, and very short relaxed reps are strides/repetition effort.
- Easy/steady/tempo/threshold are effort domains, not universal seconds-per-mile. Weather, terrain, fatigue, altitude, and workout format can justify effort rather than a pace target.
""".strip()
