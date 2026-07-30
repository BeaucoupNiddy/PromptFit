import os
import json
import re
from typing import Optional, Dict, Any, List

import requests

from webapp.pace_knowledge import (
    normalize_pace_profile,
    prompt_pace_context,
    resolve_plan_paces,
)


def _race_pace_label(race_distance: Optional[str]) -> str:
    if not race_distance:
        return "HMP"
    s = str(race_distance).strip().lower()
    if s in ("5k", "5 km", "5km"):
        return "5K pace"
    if s in ("10k", "10 km", "10km"):
        return "10K pace"
    if "marathon" in s and "half" not in s:
        return "Marathon pace"
    if "half" in s:
        return "HMP"
    return "Race pace"


def parse_prompt_to_plan(prompt: str, hmp_min_per_mile: Optional[float] = None,
                         race_distance: Optional[str] = None,
                         provider: str = "auto", model: Optional[str] = None,
                         api_key: Optional[str] = None,
                         pace_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Use OpenAI or OpenRouter to convert a freeform running plan prompt into our JSON schema.

    Returns a dict with shape:
      { "workouts": [ { name?, date?, type, duration?, distance?, intensity?, segments?, sets? }, ... ] }
    """
    pace_label = _race_pace_label(race_distance)
    athlete_paces = normalize_pace_profile(
        pace_profile,
        reference_pace=hmp_min_per_mile,
        race_distance=race_distance,
    )
    system = (
        "You are a running coach data formatter. Convert free text into JSON that STRICTLY matches this schema and conventions.\n\n"
        "Schema:\n"
        "{\n  \"workouts\": [\n    {\n      \"name\": string optional,\n      \"date\": YYYY-MM-DD optional,\n      \"type\": one of [easy, very easy, steady, easy to moderate, interval, tempo, progression, long, long run, race, special block, kenyan-style progression run],\n      \"duration\": string like '45 min' or '90 sec' (optional),\n      \"distance\": string like '8 mi', '5 km', '1000 m' (optional),\n      \"intensity\": text or percent like 'Easy', '75% of HMP', '92–95% of HMP' (optional),\n      \"segments\": [ { duration|distance, intensity, repetitions?, recovery? { duration|distance, type? } } ],\n      \"sets\": [ { repetitions, duration|distance, intensity, recovery? { duration|distance, type? }, sequence? [ { duration|distance, intensity, recovery? } ] } ]\n    }\n  ]\n}\n\n"
        "ABSOLUTE RULES:\n"
        "- Output VALID JSON only (no prose, no code fences).\n"
        "- Every workout MUST include \"type\" (do not rely on \"name\").\n"
        "- Use either \"duration\" OR \"distance\" per step (not both).\n"
        "- Use minutes/seconds for duration (e.g., '15 min', '45 sec'). Use 'mi', 'km', or 'm' for distance.\n"
        "- If a specific pace like '8:00/mi' is given, place it in \"intensity\" (e.g., '8:00/mi' or 'Easy (80% of HMP)').\n"
        "- Warmup/Cooldown MUST be modeled as segments at the beginning/end with intensity 'Very easy' (e.g., {\"duration\": '15 min', \"intensity\": 'Very easy'}).\n"
        "  DO NOT invent types like 'WU'/'CD' — use intensity 'Very easy' instead.\n"
        "- Use \"sets\" only for repeated intervals. Inside a set, use \"repetitions\" and either a single step (duration/intensity)\n"
        "  or a \"sequence\" of steps. Do NOT place warmup/cooldown inside \"sets\".\n"
        "- If a set contains multiple distances (e.g., 400m/300m/200m), represent it as ONE set with a \"sequence\" list.\n"
        "  Do NOT split into separate sets per distance.\n"
        "- If a set contains multiple timed reps (e.g., 2 min @ 105%, 1 min jog, 1 min @ 108%), use ONE set with a \"sequence\" list.\n"
        "- If the prompt says \"X sets of: [sequence]\", use one segment per set when there is between-set rest; otherwise use one set with \"repetitions\": X.\n"
        "- If between-set rest is specified, it must be a standalone segment between set segments (not recovery inside a set).\n"
        "- If each rep has its own recovery (e.g., \"1 min jog\" after each rep), attach that recovery to each sequence item.\n"
        "- Preserve ranges (e.g., '7–8 mi', '1.5–2 min') exactly in the string.\n"
        "- If the prompt specifies a rest BETWEEN sets and there are multiple sets of a ladder/sequence,\n"
        "  model each set as its own segment and place a Rest (walk/jog) segment BETWEEN them.\n"
        f"- If there is a rest/break BETWEEN sets, model it as its own segment with duration and intensity 'Rest (walk)'.\n"
        f"  Do NOT insert a bare duration-only item inside a \"sets\" list.\n"
        "- If \"segments\" is present, it should be the primary container for warmup/main/cooldown sections.\n"
        "- Keep keys minimal: type, intensity, duration/distance, repetitions, recovery (with duration/distance and type='jog'/'walk').\n"
        "- Prefer 'Very easy' for easy jogs used as warmup/cooldown or recovery; use 'Easy'/'Steady' for regular easy runs.\n\n"
        f"- When using percentages, always include the label (e.g., '92–95% of {pace_label}'), even if the user omits it.\n\n"
        "Canonical examples:\n"
        "1) Simple easy run (distance):\n"
        "{\n  \"workouts\": [ { \"type\": \"easy\", \"distance\": \"8 mi\", \"intensity\": \"Easy\" } ]\n}\n\n"
        "2) Interval workout with WU/CD and repeats:\n"
        "{\n  \"workouts\": [ {\n    \"type\": \"interval\",\n    \"segments\": [\n      { \"duration\": \"15 min\", \"intensity\": \"Very easy\" },\n      { \"sets\": [ { \"repetitions\": 5, \"duration\": \"3 min\", \"intensity\": \"105% of HMP\", \"recovery\": { \"duration\": \"1 min\", \"type\": \"jog\" } } ] },\n      { \"duration\": \"10 min\", \"intensity\": \"Very easy\" }\n    ]\n  } ]\n}\n\n"
        "3) Ladder set with rest between sets (explicit between-set rest):\n"
        "{\n  \"workouts\": [ {\n    \"type\": \"interval\",\n    \"segments\": [\n      { \"sets\": [ { \"repetitions\": 1, \"sequence\": [\n        { \"distance\": \"400 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n        { \"distance\": \"300 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n        { \"distance\": \"200 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } }\n      ] } ] },\n      { \"duration\": \"4–5 min\", \"intensity\": \"Rest (walk/jog)\" },\n      { \"sets\": [ { \"repetitions\": 1, \"sequence\": [\n        { \"distance\": \"400 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n        { \"distance\": \"300 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n        { \"distance\": \"200 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } }\n      ] } ] }\n    ]\n  } ]\n}\n\n"
        "3) Doubles (AM/PM):\n"
        "{\n  \"workouts\": [ {\n    \"date\": \"2025-08-05\",\n    \"am\": { \"type\": \"steady\", \"duration\": \"30 min\", \"intensity\": \"88% of HMP\" },\n    \"pm\": { \"type\": \"steady\", \"duration\": \"25 min\", \"intensity\": \"92% of HMP\" }\n  } ]\n}\n\n"
        "4) Long with structured segments:\n"
        "{\n  \"workouts\": [ {\n    \"type\": \"long run\",\n    \"segments\": [\n      { \"distance\": \"5 mi\", \"intensity\": \"Easy\" },\n      { \"distance\": \"5 mi\", \"intensity\": \"Easy to moderate\" },\n      { \"distance\": \"4 mi\", \"intensity\": \"95% of HMP\" }\n    ]\n  } ]\n}\n\n"
        "Quality checks:\n"
        "- Do not use custom keys ('WU','CD').\n"
        "- If both \"segments\" and \"sets\" exist, warmup/cooldown must be in \"segments\"; repeats go under a \"sets\" holder.\n"
        "- Breaks between sets must be their own segment, not an entry inside a \"sets\" list.\n"
        "- No extraneous narrative text; only the JSON body.\n"
    )
    if pace_label and pace_label != "HMP":
        system = system.replace("HMP", pace_label)
    system += "\n\n" + prompt_pace_context(athlete_paces, race_distance)
    if hmp_min_per_mile:
        system += f"\nAthlete race pace baseline is {hmp_min_per_mile:.2f} min/mi ({pace_label}). Use '% of {pace_label}' labels when intensity is relative."

    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    }

    # Provider selection
    prov = (provider or "auto").lower()
    if prov not in ("openai", "openrouter", "auto"):
        prov = "auto"

    # Use OpenAI if API key present and requested
    openai_key = api_key or os.getenv("OPENAI_API_KEY")
    or_key = api_key or os.getenv("OPENROUTER_API_KEY")

    def _try_openai() -> Optional[Dict[str, Any]]:
        nonlocal model
        key = openai_key
        if not key:
            return None
        try:
            url = "https://api.openai.com/v1/chat/completions"
            hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = dict(payload)
            body["model"] = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            body["temperature"] = 0.2
            # Encourage JSON only
            body["response_format"] = {"type": "json_object"}
            r = requests.post(url, headers=hdr, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            txt = data["choices"][0]["message"]["content"]
            return json.loads(txt)
        except Exception:
            return None

    def _try_openrouter() -> Optional[Dict[str, Any]]:
        nonlocal model
        key = or_key
        if not key:
            return None
        try:
            url = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1") + "/chat/completions"
            hdr = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            body = dict(payload)
            body["model"] = model or os.getenv("OPENROUTER_MODEL", "openrouter/auto")
            body["temperature"] = 0.2
            r = requests.post(url, headers=hdr, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            txt = data["choices"][0]["message"]["content"]
            # OpenRouter models may return code fences; strip them
            txt = txt.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(txt)
        except Exception:
            return None

    result: Optional[Dict[str, Any]] = None
    if prov in ("openai", "auto"):
        result = _try_openai()
    if result is None and prov in ("openrouter", "auto"):
        result = _try_openrouter()

    # Fallback naive parser: try to extract a single easy run like '8 mile easy @ 8:00/mi'
    if result is None:
        miles = None
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mi|miles)\b", prompt, re.I)
        if m:
            miles = float(m.group(1))
        pace = None
        p = re.search(r"(\d{1,2}):(\d{2})\s*/?\s*mi\b", prompt, re.I)
        if p:
            pace = f"{int(p.group(1))}:{int(p.group(2)):02d}/mi"
        intensity = "Easy" if "easy" in prompt.lower() else ""
        workout = {}
        if miles:
            workout["distance"] = f"{miles} mi"
        else:
            workout["duration"] = "45 min"
        if pace:
            workout["intensity"] = pace
        elif intensity:
            workout["intensity"] = intensity
        workout["type"] = "easy"
        result = {"workouts": [workout]}

    if not isinstance(result, dict) or "workouts" not in result:
        return {"workouts": []}
    return resolve_plan_paces(result, athlete_paces, race_distance)


def parse_plan_text_to_json(prompt: str,
                            provider: str = "auto",
                            model: Optional[str] = None,
                            api_key: Optional[str] = None,
                            race_distance: Optional[str] = None,
                            pace_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Use OpenAI or OpenRouter to convert a multi-week plan into plan JSON."""
    pace_label = _race_pace_label(race_distance)
    athlete_paces = normalize_pace_profile(pace_profile, race_distance=race_distance)
    system = (
        "You are a running coach data formatter. Convert a multi-week training plan into JSON that STRICTLY matches this schema and conventions.\n\n"
        "Schema:\n"
        "{\n"
        "  \"plan_meta\": { \"goal\"?: string, \"notes\"?: string, \"reference_peak_mileage\"?: number },\n"
        "  \"weeks\": [\n"
        "    {\n"
        "      \"week\": number,\n"
        "      \"phase\"?: string,\n"
        "      \"total_mileage\"?: string,\n"
        "      \"notes\"?: string,\n"
        "      \"days\": [\n"
        "        { \"day\": \"Monday\"|\"Tuesday\"|\"Wednesday\"|\"Thursday\"|\"Friday\"|\"Saturday\"|\"Sunday\", \"workout\": <workout> }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Workout schema:\n"
        "{\n"
        "  \"type\": one of [easy, very easy, steady, easy to moderate, interval, tempo, progression, long, long run, race, special block, kenyan-style progression run, rest],\n"
        "  \"duration\"?: string like '45 min' or '90 sec',\n"
        "  \"distance\"?: string like '8 mi', '5 km', '1000 m',\n"
        f"  \"intensity\"?: text like 'Easy' or '92–95% of {pace_label}',\n"
        "  \"segments\"?: [ { duration|distance, intensity, repetitions?, recovery? { duration|distance, type? } } ],\n"
        "  \"sets\"?: [ { repetitions, duration|distance, intensity, recovery? { duration|distance, type? }, sequence? [ { duration|distance, intensity, recovery? } ] } ],\n"
        "  \"am\"?: <workout>, \"pm\"?: <workout>\n"
        "}\n\n"
        "ABSOLUTE RULES:\n"
        "- Output VALID JSON only (no prose, no code fences).\n"
        "- Week numbers must be integers starting at 1 in order.\n"
        "- Days should use full names and appear in normal weekly order when possible.\n"
        "- Use either \"duration\" OR \"distance\" per step (not both).\n"
        "- Warmup/Cooldown MUST be modeled as segments with intensity 'Very easy'.\n"
        "- Use \"sets\" only for repeated intervals. Inside a set, use \"repetitions\" and either a single step or a \"sequence\".\n"
        "- If a set contains multiple distances (e.g., 400m/300m/200m), represent it as ONE set with a \"sequence\" list.\n"
        "  Do NOT split into separate sets per distance.\n"
        "- If a set contains multiple timed reps (e.g., 2 min @ 105%, 1 min jog, 1 min @ 108%), use ONE set with a \"sequence\" list.\n"
        "- If the prompt says \"X sets of: [sequence]\", use one segment per set when there is between-set rest; otherwise use one set with \"repetitions\": X.\n"
        "- If between-set rest is specified, it must be a standalone segment between set segments (not recovery inside a set).\n"
        "- If each rep has its own recovery (e.g., \"1 min jog\" after each rep), attach that recovery to each sequence item.\n"
        "- For AM/PM doubles, use \"am\" and \"pm\" fields. Do not merge them.\n"
        "- For 'Special block', set workout.type to \"special block\" and use segments; keep AM/PM on the day if present.\n"
        "- Preserve ranges (e.g., '7–8 mi', '1.5–2 min') exactly in the string.\n"
        "- If the prompt specifies a rest BETWEEN sets and there are multiple sets of a ladder/sequence,\n"
        "  model each set as its own segment and place a Rest (walk/jog) segment BETWEEN them.\n"
        "- If there is a rest/break BETWEEN sets, model it as its own segment with duration and intensity 'Rest (walk)'.\n"
        f"- When using percentages, always include the label (e.g., '92–95% of {pace_label}').\n"
        "- For rest days, set workout.type to \"rest\" with no duration/distance.\n\n"
        "Quality checks:\n"
        "- Do not use custom keys ('WU','CD').\n"
        "- Breaks between sets must be their own segment, not an entry inside a \"sets\" list.\n"
        "- No extraneous narrative text; only the JSON body.\n"
        "\nCanonical examples (compact):\n"
        "1) Mixed rep sequence inside a set:\n"
        "{\n  \"workouts\": [ { \"type\": \"interval\", \"segments\": [\n"
        "    { \"sets\": [ { \"repetitions\": 1, \"sequence\": [\n"
        "      { \"duration\": \"2 min\", \"intensity\": \"105% of HMP\", \"recovery\": { \"duration\": \"1 min\", \"type\": \"jog\" } },\n"
        "      { \"duration\": \"1 min\", \"intensity\": \"108% of HMP\", \"recovery\": { \"duration\": \"1.5 min\", \"type\": \"jog\" } }\n"
        "    ] } ] }\n"
        "  ] } ]\n}\n"
        "2) Ladder sets with between-set rest:\n"
        "{\n  \"workouts\": [ { \"type\": \"interval\", \"segments\": [\n"
        "    { \"sets\": [ { \"repetitions\": 1, \"sequence\": [\n"
        "      { \"distance\": \"400 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n"
        "      { \"distance\": \"300 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n"
        "      { \"distance\": \"200 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } }\n"
        "    ] } ] },\n"
        "    { \"duration\": \"4–5 min\", \"intensity\": \"Rest (walk/jog)\" },\n"
        "    { \"sets\": [ { \"repetitions\": 1, \"sequence\": [\n"
        "      { \"distance\": \"400 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n"
        "      { \"distance\": \"300 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } },\n"
        "      { \"distance\": \"200 m\", \"intensity\": \"110–115% of HMP\", \"recovery\": { \"duration\": \"2 min\", \"type\": \"walk\" } }\n"
        "    ] } ] }\n"
        "  ] } ]\n}\n"
    )
    system += "\n\n" + prompt_pace_context(athlete_paces, race_distance)

    prov = (provider or "auto").lower()
    if prov not in ("openai", "openrouter", "auto"):
        prov = "auto"

    openai_key = api_key or os.getenv("OPENAI_API_KEY")
    or_key = api_key or os.getenv("OPENROUTER_API_KEY")

    def _try_openai(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = openai_key
        if not key:
            return None
        try:
            url = "https://api.openai.com/v1/chat/completions"
            hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = dict(payload)
            body["model"] = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            body["temperature"] = 0.2
            body["response_format"] = {"type": "json_object"}
            r = requests.post(url, headers=hdr, json=body, timeout=90)
            r.raise_for_status()
            data = r.json()
            txt = data["choices"][0]["message"]["content"]
            return json.loads(txt)
        except Exception:
            return None

    def _try_openrouter(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = or_key
        if not key:
            return None
        try:
            url = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1") + "/chat/completions"
            hdr = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            body = dict(payload)
            body["model"] = model or os.getenv("OPENROUTER_MODEL", "openrouter/auto")
            body["temperature"] = 0.2
            r = requests.post(url, headers=hdr, json=body, timeout=90)
            r.raise_for_status()
            data = r.json()
            txt = data["choices"][0]["message"]["content"]
            txt = txt.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(txt)
        except Exception:
            return None

    def _call_llm(user_prompt: str) -> Optional[Dict[str, Any]]:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]
        }
        result: Optional[Dict[str, Any]] = None
        if prov in ("openai", "auto"):
            result = _try_openai(payload)
        if result is None and prov in ("openrouter", "auto"):
            result = _try_openrouter(payload)
        return result

    def _split_week_blocks(text: str) -> List[str]:
        lines = text.splitlines()
        week_re = re.compile(r"^\s*week\s+(\d+)\b", re.I)
        starts = []
        for i, line in enumerate(lines):
            if week_re.search(line):
                starts.append(i)
        if len(starts) <= 1:
            return [text]
        starts.append(len(lines))
        blocks: List[str] = []
        for a, b in zip(starts, starts[1:]):
            block = "\n".join(lines[a:b]).strip()
            if block:
                blocks.append(block)
        return blocks or [text]

    def _chunk_blocks(blocks: List[str], max_weeks: int = 4) -> List[str]:
        if len(blocks) <= max_weeks:
            return ["\n\n".join(blocks)]
        chunks = []
        for i in range(0, len(blocks), max_weeks):
            chunks.append("\n\n".join(blocks[i:i + max_weeks]))
        return chunks

    def _expected_start_week(block_text: str) -> Optional[int]:
        m = re.search(r"^\s*week\s+(\d+)\b", block_text, re.I | re.M)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _adjust_week_numbers(weeks: List[Dict[str, Any]], expected_start: Optional[int]) -> None:
        if not expected_start or expected_start <= 1 or not weeks:
            return
        nums = []
        for w in weeks:
            try:
                nums.append(int(w.get("week")))
            except Exception:
                return
        if not nums:
            return
        if min(nums) != 1:
            return
        max_num = max(nums)
        if max_num > len(nums):
            return
        offset = expected_start - 1
        for w in weeks:
            try:
                w["week"] = int(w.get("week")) + offset
            except Exception:
                pass

    blocks = _split_week_blocks(prompt)
    chunks = _chunk_blocks(blocks, max_weeks=4)
    results: List[Dict[str, Any]] = []
    expected_starts: List[Optional[int]] = []
    for chunk in chunks:
        expected_starts.append(_expected_start_week(chunk))
        res = _call_llm(chunk)
        if isinstance(res, dict) and "weeks" in res:
            results.append(res)

    if not results:
        return {"plan_meta": {}, "weeks": []}

    merged_meta: Dict[str, Any] = {}
    merged_weeks: List[Dict[str, Any]] = []
    seen_weeks = set()
    for res, expected in zip(results, expected_starts):
        weeks = res.get("weeks") or []
        if isinstance(weeks, list):
            _adjust_week_numbers(weeks, expected)
            for w in weeks:
                week_num = w.get("week")
                if week_num in seen_weeks:
                    continue
                seen_weeks.add(week_num)
                merged_weeks.append(w)
        meta = res.get("plan_meta") or {}
        if not merged_meta and isinstance(meta, dict) and meta:
            merged_meta = meta

    if not merged_meta:
        merged_meta = {}
    if merged_weeks and all(isinstance(w.get("week"), int) for w in merged_weeks):
        merged_weeks = sorted(merged_weeks, key=lambda w: w.get("week"))

    return resolve_plan_paces(
        {"plan_meta": merged_meta, "weeks": merged_weeks},
        athlete_paces,
        race_distance,
    )
