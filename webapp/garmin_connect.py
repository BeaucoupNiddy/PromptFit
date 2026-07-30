"""Garmin Connect workout integration (unofficial).

Garmin does not offer a public consumer API for importing workout FIT files.
This module converts the project's step model to Garmin Connect's workout JSON
and sends it through the community ``garminconnect`` package. Credentials and
MFA state are kept in memory only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional


SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}

STEP_TYPES = {
    "WARMUP": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "COOLDOWN": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "INTERVAL": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "RECOVERY": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "REPEAT": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
}

END_CONDITIONS = {
    "OPEN": {
        "conditionTypeId": 1,
        "conditionTypeKey": "lap.button",
        "displayOrder": 1,
        "displayable": True,
    },
    "TIME": {
        "conditionTypeId": 2,
        "conditionTypeKey": "time",
        "displayOrder": 2,
        "displayable": True,
    },
    "DISTANCE": {
        "conditionTypeId": 3,
        "conditionTypeKey": "distance",
        "displayOrder": 3,
        "displayable": True,
    },
    "ITERATIONS": {
        "conditionTypeId": 7,
        "conditionTypeKey": "iterations",
        "displayOrder": 7,
        "displayable": False,
    },
}

NO_TARGET = {
    "workoutTargetTypeId": 1,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}

PACE_TARGET = {
    "workoutTargetTypeId": 6,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 6,
}


class GarminMFARequired(RuntimeError):
    """Raised when Garmin requires a one-time code to finish login."""

    def __init__(
        self,
        client: Any,
        client_state: Dict[str, Any],
        tokenstore: Optional[str] = None,
    ):
        super().__init__("Garmin MFA code required")
        self.client = client
        self.client_state = client_state
        self.tokenstore = tokenstore


def _require_libs():
    try:
        from garminconnect import Garmin  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "garminconnect is not installed. Run: pip install -r requirements.txt"
        ) from exc


def _secure_tokenstore(tokenstore: str) -> str:
    path = Path(tokenstore).expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    for item in path.iterdir():
        if item.is_file():
            try:
                item.chmod(0o600)
            except OSError:
                pass
    return str(path)


def has_saved_login(tokenstore: str) -> bool:
    path = Path(tokenstore).expanduser()
    if not path.is_dir():
        return False
    return any(item.is_file() and item.stat().st_size > 0 for item in path.iterdir())


def _dump_client_tokens(client: Any, tokenstore: str) -> None:
    store = _secure_tokenstore(tokenstore)
    backend = getattr(client, "client", None) or getattr(client, "garth", None)
    dump = getattr(backend, "dump", None)
    if not callable(dump):
        raise RuntimeError("Installed garminconnect version cannot save the Garmin session")
    dump(store)
    _secure_tokenstore(store)


def login(username: str, password: str, tokenstore: Optional[str] = None):
    """Log in without prompting in the server terminal.

    If MFA is enabled, callers receive :class:`GarminMFARequired` and can
    resume the same authenticated browser session with :func:`resume_login`.
    """
    _require_libs()
    from garminconnect import Garmin

    store = _secure_tokenstore(tokenstore) if tokenstore else None
    client = Garmin(username, password, return_on_mfa=True)
    result = client.login(store)
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and result[0] == "needs_mfa"
        and isinstance(result[1], dict)
    ):
        # The retained HTTP client contains the SSO cookies needed to resume.
        # Do not retain the user's raw credentials while waiting for the code.
        try:
            client.username = None
            client.password = None
        except Exception:
            pass
        raise GarminMFARequired(client, result[1], store)
    if store:
        _dump_client_tokens(client, store)
    return client


def resume_login(
    client: Any,
    client_state: Dict[str, Any],
    mfa_code: str,
    tokenstore: Optional[str] = None,
):
    """Finish an MFA login and return the authenticated Garmin client."""
    code = (mfa_code or "").strip()
    if not code:
        raise ValueError("Enter the Garmin verification code")
    fn = getattr(client, "resume_login", None)
    if not callable(fn):
        raise RuntimeError("Installed garminconnect version does not support web MFA")
    fn(client_state, code)
    if tokenstore:
        _dump_client_tokens(client, tokenstore)
    return client


def login_saved(tokenstore: str):
    """Restore a previously connected Garmin session without a password."""
    if not has_saved_login(tokenstore):
        raise RuntimeError("Garmin is not connected. Connect it once on this Mac first.")
    _require_libs()
    from garminconnect import Garmin

    store = _secure_tokenstore(tokenstore)
    client = Garmin()
    try:
        client.login(store)
    except Exception as exc:
        raise RuntimeError(
            "The saved Garmin connection expired or was rejected. Reconnect Garmin on this Mac."
        ) from exc
    _dump_client_tokens(client, store)
    return client


def disconnect_saved(tokenstore: str) -> None:
    """Remove only this app's locally saved Garmin session tokens."""
    path = Path(tokenstore).expanduser().resolve()
    if not path.is_dir():
        return
    try:
        from garminconnect import Garmin

        Garmin().logout(str(path))
    except Exception:
        # Compatibility cleanup for older library token filenames.
        for pattern in ("garmin_tokens.json", "oauth1_token.json", "oauth2_token.json"):
            candidate = path / pattern
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def _target_values(st: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    ttype = str(st.get("targetType") or "NO_TARGET").upper()
    if ttype not in ("SPEED", "PACE"):
        return None, None

    low = st.get("targetValueLow")
    high = st.get("targetValueHigh")
    try:
        low_val = float(low)
        high_val = float(high)
    except (TypeError, ValueError):
        return None, None

    if ttype == "PACE":
        # The project represents PACE target values as seconds/km. Garmin's
        # workout service expects the custom pace range as speed in m/s.
        if low_val <= 0 or high_val <= 0:
            return None, None
        low_val, high_val = 1000.0 / low_val, 1000.0 / high_val

    low_val, high_val = min(low_val, high_val), max(low_val, high_val)
    if low_val <= 0 or high_val <= 0:
        return None, None
    return low_val, high_val


def _step_type(st: Dict[str, Any]) -> str:
    blob = f"{st.get('type') or ''} {st.get('description') or ''}".lower()
    if "warm" in blob:
        return "WARMUP"
    if "cool" in blob:
        return "COOLDOWN"
    if any(word in blob for word in ("recovery", "rest", "jog", "walk")):
        return "RECOVERY"
    return "INTERVAL"


def _executable_step(st: Dict[str, Any]) -> Dict[str, Any]:
    duration_type = str(st.get("endCondition") or "OPEN").upper()
    if duration_type not in ("TIME", "DISTANCE"):
        duration_type = "OPEN"

    step: Dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepType": dict(STEP_TYPES[_step_type(st)]),
        "endCondition": dict(END_CONDITIONS[duration_type]),
        "targetType": dict(NO_TARGET),
    }
    description = str(st.get("description") or "").strip()[:200]
    if description:
        step["description"] = description
    if duration_type in ("TIME", "DISTANCE"):
        try:
            value = float(st.get("endConditionValue") or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise ValueError(f"{duration_type.lower()} workout step must be greater than zero")
        step["endConditionValue"] = value

    target_low, target_high = _target_values(st)
    if target_low is not None and target_high is not None:
        step["targetType"] = dict(PACE_TARGET)
        step["targetValueOne"] = target_low
        step["targetValueTwo"] = target_high
    return step


def _repeat_meta(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if isinstance(st.get("repeat"), dict):
        return dict(st["repeat"])
    if str(st.get("type") or "").lower() == "repeat":
        return {}
    return None


def _workout_step_tree(steps: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Turn FIT-style trailing repeat markers into Garmin repeat groups."""
    tree: list[Dict[str, Any]] = []
    for source_index, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        repeat = _repeat_meta(st)
        if repeat is None:
            tree.append(_executable_step(st))
            continue

        try:
            start_index = int(repeat.get("start_index"))
        except (TypeError, ValueError):
            start_index = -1
        try:
            block_len = int(repeat.get("block_len") or repeat.get("steps") or 0)
        except (TypeError, ValueError):
            block_len = 0
        if block_len <= 0 and start_index >= 0:
            block_len = source_index - start_index
        if block_len <= 0:
            block_len = 1
        try:
            count = int(repeat.get("count") or repeat.get("repeat_count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, count)

        if block_len > len(tree):
            raise ValueError("repeat block points outside the available workout steps")
        children = tree[-block_len:]
        del tree[-block_len:]
        tree.append({
            "type": "RepeatGroupDTO",
            "stepType": dict(STEP_TYPES["REPEAT"]),
            "numberOfIterations": count,
            "workoutSteps": children,
            "endCondition": dict(END_CONDITIONS["ITERATIONS"]),
            "endConditionValue": float(count),
            "smartRepeat": False,
        })

    order = 0

    def assign(items: list[Dict[str, Any]]) -> None:
        nonlocal order
        for item in items:
            order += 1
            item["stepOrder"] = order
            if item.get("type") == "RepeatGroupDTO":
                assign(item.get("workoutSteps") or [])

    assign(tree)
    return tree


def _estimated_duration(items: list[Dict[str, Any]]) -> int:
    def total_seconds(rows: list[Dict[str, Any]]) -> float:
        total = 0.0
        for item in rows:
            if item.get("type") == "RepeatGroupDTO":
                count = max(1, int(item.get("numberOfIterations") or 1))
                total += count * total_seconds(item.get("workoutSteps") or [])
            elif (item.get("endCondition") or {}).get("conditionTypeKey") == "time":
                total += float(item.get("endConditionValue") or 0)
            elif (item.get("endCondition") or {}).get("conditionTypeKey") == "distance":
                target_low = item.get("targetValueOne")
                target_high = item.get("targetValueTwo")
                try:
                    speed = (float(target_low) + float(target_high)) / 2.0
                except (TypeError, ValueError):
                    speed = 3.0
                if speed > 0:
                    total += float(item.get("endConditionValue") or 0) / speed
        return total

    total = total_seconds(items)
    return max(1, int(round(total)))


def create_workout_json(name: str, steps: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the Garmin Connect workout-service payload for a running workout."""
    workout_steps = _workout_step_tree(steps)
    if not workout_steps:
        raise ValueError("No workout steps to upload")
    clean_name = " ".join(str(name or "Workout").split())[:80] or "Workout"
    return {
        "workoutName": clean_name,
        "sportType": dict(SPORT_RUNNING),
        "estimatedDurationInSecs": _estimated_duration(workout_steps),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(SPORT_RUNNING),
            "workoutSteps": workout_steps,
        }],
        "author": {},
    }


def _response_json(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    json_fn = getattr(response, "json", None)
    if callable(json_fn):
        try:
            value = json_fn()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _schedule_workout(client: Any, workout_id: Any, schedule_date: date) -> Dict[str, Any]:
    fn = getattr(client, "schedule_workout", None)
    if callable(fn):
        return _response_json(fn(workout_id, schedule_date.isoformat()))

    # Compatibility with garminconnect 0.2.30, which has upload_workout but
    # predates the schedule_workout convenience method.
    backend = getattr(client, "client", None) or getattr(client, "garth", None)
    post = getattr(backend, "post", None)
    if callable(post):
        response = post(
            "connectapi",
            f"/workout-service/schedule/{workout_id}",
            json={"date": schedule_date.isoformat()},
            api=True,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return _response_json(response)
    raise RuntimeError("Installed garminconnect version cannot schedule workouts")


def unschedule_workout(client: Any, scheduled_workout_id: Any) -> None:
    """Remove one scheduled instance while supporting older garminconnect releases."""
    fn = getattr(client, "unschedule_workout", None)
    if callable(fn):
        fn(scheduled_workout_id)
        return
    backend = getattr(client, "client", None) or getattr(client, "garth", None)
    delete = getattr(backend, "delete", None)
    if callable(delete):
        response = delete(
            "connectapi",
            f"/workout-service/schedule/{scheduled_workout_id}",
            api=True,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return
    raise RuntimeError("Installed garminconnect version cannot remove scheduled workouts")


def delete_workout(client: Any, workout_id: Any) -> None:
    """Delete one workout template while supporting older garminconnect releases."""
    fn = getattr(client, "delete_workout", None)
    if callable(fn):
        fn(workout_id)
        return
    backend = getattr(client, "client", None) or getattr(client, "garth", None)
    delete = getattr(backend, "delete", None)
    if callable(delete):
        response = delete(
            "connectapi",
            f"/workout-service/workout/{workout_id}",
            api=True,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return
    raise RuntimeError("Installed garminconnect version cannot delete workouts")


def upload_and_optionally_schedule(
    client: Any,
    workout_json: Dict[str, Any],
    schedule_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Create a workout in Garmin Connect and optionally put it on the calendar."""
    upload = getattr(client, "upload_workout", None)
    if callable(upload):
        created = upload(workout_json)
    else:
        backend = getattr(client, "client", None) or getattr(client, "garth", None)
        post = getattr(backend, "post", None)
        if not callable(post):
            raise RuntimeError(
                "Installed garminconnect version does not support workout upload; "
                "run: pip install -r requirements.txt"
            )
        response = post(
            "connectapi",
            "/workout-service/workout",
            json=workout_json,
            api=True,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        created = _response_json(response)
    if not isinstance(created, dict):
        raise RuntimeError("Garmin returned an unexpected response while creating the workout")
    workout_id = created.get("workoutId") or created.get("workoutUUID") or created.get("id")
    if not workout_id:
        raise RuntimeError("Garmin created no identifiable workout")

    result: Dict[str, Any] = {
        "workoutId": workout_id,
        "workoutName": created.get("workoutName") or workout_json.get("workoutName"),
        "scheduled": False,
    }
    if schedule_date:
        try:
            scheduled = _schedule_workout(client, workout_id, schedule_date)
            result["scheduled"] = True
            result["scheduleDate"] = schedule_date.isoformat()
            schedule_id = (
                scheduled.get("workoutScheduleId")
                or scheduled.get("scheduledWorkoutId")
                or scheduled.get("id")
            )
            if schedule_id:
                result["workoutScheduleId"] = schedule_id
        except Exception as exc:
            # The workout itself is safely in the account even if calendar
            # scheduling fails, so return an explicit partial-success warning.
            result["scheduleError"] = str(exc)
    return result
