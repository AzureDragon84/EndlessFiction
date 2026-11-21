"""The notebook's Gemini key pool, rebuilt inside the server.

The notebook does not treat a provider as one account with one key. It treats a
*unit* as one model on one key, each with its own rpm/tpm/rpd, and keeps a
per-unit daily ledger on Drive (`pool_state.json`). A task (`translate`,
`extract`, `conflicts`) names a chain of models; the pool hands out the unit
with the most room, reports the tokens it spent, and when a key says 429 puts
just that key on a short cooldown. When every unit is out of daily requests the
run pauses until midnight in `reset_tz` instead of failing.

This service is that pool. It owns the ledger, leases a unit per call, and
projects the pool into the external translator package's YAML: one provider per
key (so the package rate-limits each key separately) and one engine per unit.
lncrawl pins `engine=<unit>` for every call, so no routing lane is written —
the pool decides, exactly as the notebook does, and the package's own router
never quietly falls back to another model.

The key text itself never leaves the config file and never appears in a status
response or a log line; only labels do.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import json
import logging
import os
import re
from threading import RLock
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4
import zoneinfo

import requests
import yaml

from ..context import ctx
from ..exceptions import ServerErrors

logger = logging.getLogger(__name__)

# Tasks the pool understands: `translate` covers chapters/novels, and `extract`
# is both the paid extraction mode and the glossary decision pass. A task with
# no chain of its own uses the translate one.
TASKS = ("translate", "extract")

# Notebook defaults for a free Gemini key.
_DEFAULT_MODEL = "gemini-3.1-flash-lite"
_DEFAULT_RPM = 15
_DEFAULT_TPM = 250_000
_DEFAULT_RPD = 500

# How long one key rests after a 429 before it is tried again.
_RATE_COOLDOWN = 60.0

_LOG_LIMIT = 200

# A health probe is one tiny chat request per model on one key. Keys are checked
# in parallel because a dead key answers in milliseconds while a live one takes
# about a second, and an admin should not wait a minute for 16 keys.
_PROBE_TIMEOUT = 25
_PROBE_WORKERS = 6

# A probe's states that mean the key itself is unusable, as opposed to a model
# it merely cannot see (`no_model`).
_KEY_BAD = ("expired", "invalid", "blocked", "billing", "unreachable", "unknown")


class PoolExhausted(Exception):
    """Every unit that could serve this task is out of quota right now.

    `retry_after_seconds` is when the daily counters reset, so a handler can
    pause its job for exactly that long instead of failing it.
    """

    def __init__(self, retry_after_seconds: int, message: str = "") -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(message or "all Gemini keys have used their quota")


@dataclass(frozen=True)
class Unit:
    """One model on one key: the notebook's unit of quota."""

    model: str
    label: str
    key: str
    rpm: float
    tpm: int
    rpd: int

    @property
    def id(self) -> str:
        return f"{self.model}|{self.label}"

    @property
    def engine_id(self) -> str:
        """The translator-package engine id this unit is pinned to."""
        return f"{self.model}@{self.label}"

    @property
    def provider_id(self) -> str:
        return f"key:{self.label}"


def classify_error(status_code: int, text: str) -> Tuple[str, str]:
    """`(state, message)` for one Google answer.

    Google marks the reason it refused in an `ErrorInfo.details` entry as well as
    the human message, and the pair is what tells an expired key apart from a
    spent daily quota. Matching the message too keeps a proxy or a gateway that
    only forwards text from turning every failure into `unknown`.
    """
    if status_code < 400:
        return "ok", ""

    message = ""
    reason = ""
    body = (text or "").strip()
    try:
        data = json.loads(body)
        # Google wraps these in a one-element list on some endpoints.
        if isinstance(data, list) and data:
            data = data[0]
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            reason = str(error.get("status") or "")
            for detail in error.get("details") or []:
                if isinstance(detail, dict) and detail.get("reason"):
                    reason = str(detail["reason"])
                    break
        elif isinstance(error, str):
            message = error
    except Exception:
        message = ""
    detail = (" ".join(message.split()) or " ".join(body.split()))[:200]

    haystack = f"{reason} {message} {body[:400]}".lower()
    compact = re.sub(r"[\s_\-]+", "", haystack)
    if "apikeyexpired" in compact or "api key expired" in haystack or "key has expired" in haystack:
        return "expired", detail or "The API key has expired"
    if (
        "apikeyinvalid" in compact
        or "api key not valid" in haystack
        or "invalid api key" in haystack
        or "invalid auth" in haystack
        or ("apikey" in compact and "invalid" in compact)
    ):
        return "invalid", detail or "The API key is not valid"
    if status_code == 401:
        return "invalid", detail or "The API key is not valid"
    if "apikeyserviceblocked" in compact or "api key is blocked" in haystack:
        return "blocked", detail or "Google blocked this API key"
    if "service_disabled" in haystack or "billing" in haystack or "not enabled" in haystack:
        return "billing", detail or "The project behind this key has the API disabled"
    if status_code == 403:
        return "blocked", detail or "Google refused this key"
    if status_code == 429:
        if "perday" in compact or "daily" in compact:
            return "quota_spent", detail or "The daily quota is spent"
        return "rate_limited", detail or "Too many requests this minute"
    if status_code == 404 or "is not found" in haystack or "not supported" in haystack:
        return "no_model", detail or "This key cannot use that model"
    if status_code >= 500 or "overloaded" in haystack or "unavailable" in haystack:
        return "overloaded", detail or "The model is busy at Google"
    return "unknown", detail or f"HTTP {status_code}"


@dataclass(frozen=True)
class Lease:
    unit: Unit
    estimated_tokens: int


def parse_keys(text: str) -> List[Tuple[str, str]]:
    """`(label, key)` pairs from the notebook's one-key-per-line box.

    `AIza...` becomes `GEMINI_KEY_1`; `LABEL=AIza...` and `AIza...,projectA`
    keep the name the user chose. A repeated label is suffixed rather than
    dropped, because two keys are two quotas even when they share a project.
    """
    pairs: List[Tuple[str, str]] = []
    seen: set[str] = set()
    number = 0
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        key = line
        if "=" in line:
            label, key = line.split("=", 1)
            label = label.strip()
        elif "," in line:
            key, label = line.split(",", 1)
            label = label.strip()
        key = key.strip().strip('"').strip("'")
        if not key:
            continue
        number += 1
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_")
        label = label or f"GEMINI_KEY_{number}"
        base, suffix = label, 2
        while label in seen:
            label = f"{base}_{suffix}"
            suffix += 1
        seen.add(label)
        pairs.append((label, key))
    return pairs


class KeyPoolService:
    """Ledger, selection, and translator-config projection for the key pool."""

    def __init__(self) -> None:
        self._lock = RLock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def keys(self) -> List[Tuple[str, str]]:
        return parse_keys(ctx.config.translator.api_keys)

    def models(self) -> List[Dict[str, Any]]:
        """Configured model blocks, or one default block covering every key.

        The notebook wires keys into the pool as soon as they are saved; an
        empty `pool_models` does the same here, so pasting keys is enough.
        """
        labels = [label for label, _ in self.keys()]
        configured = [
            entry
            for entry in (ctx.config.translator.pool_models or [])
            if isinstance(entry, dict) and entry.get("model")
        ]
        if not configured:
            if not labels:
                return []
            configured = [
                {
                    "model": _DEFAULT_MODEL,
                    "keys": labels,
                    "rpm": _DEFAULT_RPM,
                    "tpm": _DEFAULT_TPM,
                    "rpd": _DEFAULT_RPD,
                }
            ]

        resolved: List[Dict[str, Any]] = []
        for entry in configured:
            wanted = entry.get("keys") or labels
            resolved.append(
                {
                    "model": str(entry["model"]).strip(),
                    "keys": [str(label) for label in wanted],
                    "rpm": float(entry.get("rpm") or _DEFAULT_RPM),
                    "tpm": int(entry.get("tpm") or _DEFAULT_TPM),
                    "rpd": int(entry.get("rpd") or _DEFAULT_RPD),
                }
            )
        return resolved

    def units(self, model: Optional[str] = None) -> List[Unit]:
        """Every model×key pair whose key is actually saved.

        `model` narrows the pool to that one model, which is how a pinned run is
        kept from reaching another model even when its own units are spent.
        """
        keys = dict(self.keys())
        units: List[Unit] = []
        seen: set[str] = set()
        for entry in self.models():
            if model is not None and str(entry["model"]) != model:
                continue
            for label in entry["keys"]:
                key = keys.get(label)
                if not key:
                    continue
                unit = Unit(
                    model=entry["model"],
                    label=label,
                    key=key,
                    rpm=entry["rpm"],
                    tpm=entry["tpm"],
                    rpd=entry["rpd"],
                )
                if unit.id not in seen:
                    seen.add(unit.id)
                    units.append(unit)
        return units

    def pin(self, model: Optional[str]) -> Optional[str]:
        """A pinned model id, or a 422 when the pool does not list that model.

        A pin promises the run may spend this model and nothing else, so an id
        the pool does not have is a typo rather than a reason to fall back to
        the task chain. Validating in one place keeps the endpoints, the chain
        edit and `generate` from disagreeing about what "in the pool" means.
        """
        if model is None:
            return None
        available = [str(entry["model"]) for entry in self.models()]
        if model not in available:
            raise ServerErrors.invalid_input.with_extra(
                f"{model} is not in the key pool. Add it under Keys & models first."
            )
        return model

    def chain(self, task: str = "translate") -> List[str]:
        """Model ids this task may use, in priority order."""
        available = [entry["model"] for entry in self.models()]
        tasks = ctx.config.translator.pool_tasks or {}
        chosen = tasks.get(task) or tasks.get("translate") or available
        return [str(model) for model in chosen if str(model) in available]

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    def _state_path(self) -> str:
        return ctx.config.translator.pool_state_file

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(self._state_path(), encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("units"), dict):
                return data
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Ignoring unreadable pool state; starting a new ledger", exc_info=True)
        return {"version": 1, "units": {}, "log": []}

    def _save_state(self, state: Dict[str, Any]) -> None:
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{uuid4().hex[:6]}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            logger.warning("Unable to persist the key pool ledger", exc_info=True)

    def _tz(self) -> tzinfo:
        try:
            return zoneinfo.ZoneInfo(ctx.config.translator.reset_tz)
        except Exception:
            return timezone.utc

    def _today(self) -> str:
        return datetime.now(self._tz()).date().isoformat()

    def reset_at(self) -> datetime:
        """Midnight in the configured timezone: when the daily counters reset."""
        now = datetime.now(self._tz())
        tomorrow = (now + timedelta(days=1)).date()
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=self._tz())

    def resume_seconds(self) -> int:
        return max(1, int((self.reset_at() - datetime.now(self._tz())).total_seconds()))

    @staticmethod
    def _row(state: Dict[str, Any], unit: Unit, day: str) -> Dict[str, Any]:
        row = state["units"].get(unit.id)
        if not isinstance(row, dict) or row.get("day") != day:
            row = {
                "day": day,
                "used": 0,
                "tokens": 0,
                "calls": 0,
                "in": 0,
                "out": 0,
                "errors": 0,
                "exhausted": False,
                "cooldown_until": 0.0,
                "minutes": {},
            }
            state["units"][unit.id] = row
        return row

    @staticmethod
    def _minute(row: Dict[str, Any], now: float) -> Tuple[int, int]:
        """(calls, tokens) over the last rolling minute."""
        minutes = row.get("minutes") or {}
        current = int(now // 60)
        calls = tokens = 0
        for key in list(minutes):
            try:
                bucket = int(key)
            except (TypeError, ValueError):
                minutes.pop(key, None)
                continue
            if current - bucket > 1:
                minutes.pop(key, None)
                continue
            entry = minutes.get(key) or [0, 0]
            calls += int(entry[0])
            tokens += int(entry[1])
        return calls, tokens

    def _free(self, unit: Unit, row: Dict[str, Any], now: float) -> Tuple[int, int]:
        """(free requests this minute, free tokens this minute) for a unit."""
        calls, tokens = self._minute(row, now)
        return max(0, int(unit.rpm) - calls), max(0, unit.tpm - tokens)

    def _eligible(self, unit: Unit, row: Dict[str, Any], now: float) -> bool:
        if row.get("exhausted") or row.get("used", 0) >= unit.rpd:
            return False
        if float(row.get("cooldown_until") or 0) > now:
            return False
        free_requests, free_tokens = self._free(unit, row, now)
        return free_requests > 0 and free_tokens > 0

    # ------------------------------------------------------------------
    # Key health
    # ------------------------------------------------------------------

    def _health_path(self) -> str:
        """Per-unit health lives beside the ledger, never in the config."""
        return os.path.join(os.path.dirname(self._state_path()), "key_health.json")

    def _load_health(self) -> Dict[str, Any]:
        try:
            with open(self._health_path(), encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("units"), dict):
                return data
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Ignoring unreadable key health; starting over", exc_info=True)
        return {"version": 1, "units": {}}

    def _save_health(self, data: Dict[str, Any]) -> None:
        path = self._health_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{uuid4().hex[:6]}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            logger.warning("Unable to persist key health", exc_info=True)

    def _record_health(self, entries: Dict[str, Dict[str, Any]]) -> None:
        if not entries:
            return
        with self._lock:
            data = self._load_health()
            data.setdefault("units", {}).update(entries)
            self._save_health(data)

    def health(self) -> Dict[str, Any]:
        """Last known state of every unit, keyed by `<model>|<label>`."""
        return self._load_health()

    @staticmethod
    def _entry(data: Dict[str, Any], unit: Unit) -> Dict[str, Any]:
        raw = (data.get("units") or {}).get(unit.id)
        entry = raw if isinstance(raw, dict) else {}
        return {
            "state": str(entry.get("state") or "unchecked"),
            "detail": str(entry.get("detail") or ""),
            "checked_at": float(entry.get("checked_at") or 0),
        }

    def _models_url(self) -> str:
        """The native model list: no generation, so a probe costs no quota."""
        base = ctx.config.translator.gemini_base_url.rstrip("/")
        if base.endswith("/openai"):
            base = base[: -len("/openai")]
        return f"{base}/models"

    @staticmethod
    def _check_key(key: str, url: str) -> Tuple[str, str, set[str]]:
        """`(state, detail, models)` for one key.

        Listing the models a key can see answers the two questions that matter
        without spending a request: is the key itself still good, and which of
        the pool's models it is actually allowed to call. The key travels in the
        `x-goog-api-key` header, never the URL, so an error string can be shown
        to an admin without a key ending up in it.
        """
        try:
            response = requests.get(url, headers={"x-goog-api-key": key}, timeout=_PROBE_TIMEOUT)
        except Exception as error:
            return "unreachable", " ".join(str(error).split())[:200], set()
        if response.status_code >= 400:
            state, detail = classify_error(response.status_code, response.text)
            return state, detail, set()
        try:
            payload = response.json()
            entries = payload.get("models") if isinstance(payload, dict) else None
            names = {
                str(entry.get("name") or "").split("/")[-1]
                for entry in entries or []
                if isinstance(entry, dict)
            }
        except Exception:
            return "unknown", "Google answered with something that is not a model list", set()
        return "ok", "", {name for name in names if name}

    def probe(
        self,
        labels: Optional[Sequence[str]] = None,
        models: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Ask Google about every key and record whether it is still usable.

        This is the admin screen's "is this key good" button. It lists the
        models a key may call instead of generating anything, so it is instant,
        spends no daily quota, and still separates a valid key from an expired
        or invalid one, a blocked or billing-disabled project, and a key that
        simply cannot see one of the pool's models.

        Rate limits and spent quotas are not probed: the ledger already knows
        them from real calls, and a probe must not wake a cooling key.

        Requests go through `requests` directly rather than `ctx.http`, because
        the shared scraper session is not built for several threads at once and
        a probe must never disturb a running translation job.
        """
        wanted_labels = {str(label) for label in labels or ()}
        wanted_models = {str(model) for model in models or ()}
        all_units = self.units()
        units = [
            unit
            for unit in all_units
            if (not wanted_labels or unit.label in wanted_labels)
            and (not wanted_models or unit.model in wanted_models)
        ]
        if not units:
            return self._load_health()

        wanted = {unit.label: unit.key for unit in units}
        url = self._models_url()
        checked: Dict[str, Tuple[str, str, set[str]]] = {}
        with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
            results = pool.map(lambda item: self._check_key(item[1], url), wanted.items())
            for label, result in zip(list(wanted), results):
                checked[label] = result

        now = time.time()
        entries: Dict[str, Dict[str, Any]] = {}
        for unit in units:
            state, detail, names = checked.get(unit.label, ("unreachable", "", set()))
            if state == "ok" and unit.model not in names:
                state = "no_model"
                detail = "The key works, but Google does not offer this model to it"
            entries[unit.id] = {"state": state, "detail": detail, "checked_at": now}
        self._record_health(entries)
        logger.info(f"Key pool: checked {len({unit.label for unit in units})} key(s)")
        return self._load_health()

    # ------------------------------------------------------------------
    # Selection and reporting
    # ------------------------------------------------------------------

    def lease(
        self,
        task: str = "translate",
        *,
        estimated_tokens: int = 0,
        exclude: Sequence[str] = (),
        units: Optional[List[Unit]] = None,
        model: Optional[str] = None,
    ) -> Optional[Lease]:
        """The unit with the most headroom that can take this call, or None.

        The notebook spreads work over keys by remaining quota rather than
        round-robin, which keeps every key's daily window usable at once.

        `model` replaces the task chain for this call: a pinned run may use the
        model it named even when the chain does not list it.
        """
        chain = set(self.chain(task)) if model is None else {model}
        pool = units if units is not None else self.units()
        candidates = [unit for unit in pool if unit.model in chain and unit.id not in exclude]
        if not candidates:
            return None
        now = time.time()
        day = self._today()
        with self._lock:
            state = self._load_state()
            usable: List[Tuple[int, Unit]] = []
            for unit in candidates:
                row = self._row(state, unit, day)
                if not self._eligible(unit, row, now):
                    continue
                free_requests, _free_tokens = self._free(unit, row, now)
                usable.append((free_requests, unit))
            if not usable:
                self._save_state(state)
                return None
            usable.sort(
                key=lambda item: (-item[0], item[1].rpd - state["units"][item[1].id]["used"])
            )
            unit = usable[0][1]
            return Lease(unit=unit, estimated_tokens=max(0, estimated_tokens))

    def report(
        self,
        lease: Lease,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: Optional[str] = None,
        detail: str = "",
    ) -> None:
        """Record one call. `error` is `rate`, `quota`, or `failed`."""
        unit = lease.unit
        now = time.time()
        day = self._today()
        with self._lock:
            state = self._load_state()
            row = self._row(state, unit, day)
            if error:
                row["errors"] = int(row.get("errors") or 0) + 1
                if error == "quota":
                    row["exhausted"] = True
                elif error == "rate":
                    row["cooldown_until"] = now + _RATE_COOLDOWN
                else:
                    row["cooldown_until"] = now + _RATE_COOLDOWN
                self._log(state, f"{unit.model}/{unit.label}: {error} {detail}".strip())
            else:
                row["used"] = int(row.get("used") or 0) + 1
                row["calls"] = int(row.get("calls") or 0) + 1
                row["in"] = int(row.get("in") or 0) + max(0, tokens_in)
                row["out"] = int(row.get("out") or 0) + max(0, tokens_out)
                row["tokens"] = int(row.get("tokens") or 0) + max(0, tokens_in + tokens_out)
                minutes = row.setdefault("minutes", {})
                bucket = str(int(now // 60))
                entry = minutes.setdefault(bucket, [0, 0])
                entry[0] = int(entry[0]) + 1
                entry[1] = int(entry[1]) + max(0, tokens_in + tokens_out)
                if row["used"] >= unit.rpd:
                    row["exhausted"] = True
            self._save_state(state)

    def _log(self, state: Dict[str, Any], line: str) -> None:
        stamp = datetime.now(self._tz()).strftime("%H:%M:%S")
        log = state.setdefault("log", [])
        log.append(f"{stamp} {line}")
        del log[:-_LOG_LIMIT]

    def all_exhausted(self, task: str = "translate") -> bool:
        """True when nothing in the chain has daily requests left.

        A short cooldown does not count: that key will come back in a minute,
        while an exhausted day needs the reset to pass.
        """
        chain = set(self.chain(task))
        pool = [unit for unit in self.units() if unit.model in chain]
        if not pool:
            return True
        day = self._today()
        with self._lock:
            state = self._load_state()
            for unit in pool:
                row = self._row(state, unit, day)
                if int(row.get("used") or 0) < unit.rpd and not row.get("exhausted"):
                    return False
        return True

    # ------------------------------------------------------------------
    # Status and projection
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Pool rows for the admin screen. Never includes a key value."""
        now = time.time()
        day = self._today()
        units = self.units()
        health = self._load_health()
        with self._lock:
            state = self._load_state()
            rows = []
            for unit in units:
                row = self._row(state, unit, day)
                free_requests, free_tokens = self._free(unit, row, now)
                rows.append(
                    {
                        "model": unit.model,
                        "project": unit.label,
                        "key_saved": bool(unit.key),
                        "state": (
                            "exhausted"
                            if row.get("exhausted") or row.get("used", 0) >= unit.rpd
                            else "cooling"
                            if float(row.get("cooldown_until") or 0) > now
                            else "ok"
                        ),
                        "used_today": int(row.get("used") or 0),
                        "rpd": unit.rpd,
                        "rpm": int(unit.rpm),
                        "free_rpm": free_requests,
                        "free_tpm": free_tokens,
                        "tokens_today": int(row.get("tokens") or 0),
                        "errors": int(row.get("errors") or 0),
                        "calls": int(row.get("calls") or 0),
                        "in": int(row.get("in") or 0),
                        "out": int(row.get("out") or 0),
                        "health": self._entry(health, unit),
                    }
                )
            log = list(state.get("log") or [])[-20:]
            self._save_state(state)

        keys: List[Dict[str, Any]] = []
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for unit, row in zip(units, rows):
            by_label.setdefault(unit.label, []).append(
                {
                    "model": unit.model,
                    **self._entry(health, unit),
                    "quota_state": row["state"],
                    "used_today": row["used_today"],
                    "rpd": row["rpd"],
                    "free_today": max(0, row["rpd"] - row["used_today"]),
                }
            )
        for label, checked in by_label.items():
            bad = next((item for item in checked if item["state"] in _KEY_BAD), None)
            quota = {item["quota_state"] for item in checked}
            throttled = next((item for item in checked if item["state"] == "rate_limited"), None)
            if bad:
                state, detail = bad["state"], bad["detail"]
            elif throttled:
                state, detail = (
                    "rate_limited",
                    throttled["detail"] or "Google is throttling this key",
                )
            elif "cooling" in quota:
                state = "rate_limited"
                detail = "Resting after a 429; it is tried again within a minute"
            elif quota == {"exhausted"}:
                state = "quota_spent"
                detail = "Every model on this key has spent today's quota"
            elif all(item["state"] == "unchecked" for item in checked):
                state, detail = "unchecked", ""
            else:
                state, detail = "ok", ""
            keys.append(
                {
                    "label": label,
                    "state": state,
                    "detail": detail,
                    "checked_at": max((item["checked_at"] for item in checked), default=0.0),
                    "used_today": sum(item["used_today"] for item in checked),
                    "rpd": sum(item["rpd"] for item in checked),
                    "free_today": sum(item["free_today"] for item in checked),
                    "units": checked,
                }
            )
        return {
            "configured": bool(units),
            "paused": bool(
                ctx.config.translator.auto_pause_on_daily and units and self.all_exhausted()
            ),
            "reset_at": self.reset_at().isoformat(),
            "reset_in_seconds": self.resume_seconds(),
            "reset_tz": ctx.config.translator.reset_tz,
            "chains": {task: self.chain(task) for task in TASKS},
            "tasks": list(TASKS),
            "models": list(ctx.config.translator.pool_models or []),
            "settings": {
                "enabled": ctx.config.translator.enabled,
                "auto_pause_on_daily": ctx.config.translator.auto_pause_on_daily,
                "reset_tz": ctx.config.translator.reset_tz,
                "max_request_tokens": ctx.config.translator.max_request_tokens,
                "web_engine_order": list(ctx.config.translator.web_engine_order or []),
            },
            "units": rows,
            "key_health": keys,
            "log": log,
        }

    def clear(self) -> Dict[str, Any]:
        """Forget every key, and the ledger and health rows that belonged to them.

        The dashboard is write-only for keys, and a key pasted once would keep
        spending quota forever. Clearing the saved text also clears the files
        that would otherwise hand a freshly pasted key the old key's counters.
        Models, chains and settings are untouched: only the keys go.
        """
        with self._lock:
            ctx.config.translator.api_keys = ""
            for path in (self._state_path(), self._health_path()):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.warning(f"Unable to remove {path}", exc_info=True)
        self.ensure_config(purge=True)
        logger.info("Key pool: removed every saved key")
        return self.status()

    def estimate(self, chapters: int, chars_per_chapter: int = 4000) -> Dict[str, Any]:
        """Pre-flight: calls and headroom for a run, before spending anything.

        Two calls per chapter is what the notebook reports (body + title); a
        body is split when it exceeds the per-request token budget.
        """
        per_request = max(1000, ctx.config.translator.max_request_tokens)
        chunks = max(1, -(-max(1, chars_per_chapter) // (per_request * 4)))
        calls = max(0, int(chapters)) * (chunks + 1)
        capacity = sum(max(0, unit.rpd) for unit in self.units())
        used = sum(row["used_today"] for row in self.status()["units"])
        remaining = max(0, capacity - used)
        return {
            "chapters": max(0, int(chapters)),
            "calls": calls,
            "remaining_calls_today": remaining,
            "enough": remaining >= calls,
            "resume_at": self.reset_at().isoformat(),
            "reset_in_seconds": self.resume_seconds(),
        }

    def ensure_config(self, purge: bool = False) -> bool:
        """Project the pool into the translator package's YAML.

        Returns True when the file changed. Only pool-owned providers/engines
        are written, plus the keyless Bing lane the glossary pre-pass uses;
        nothing is written while no key is saved, so an existing hand-written
        config keeps working until a pool exists. `purge` is the exception: it
        rewrites the file with no keyed provider left, so a cleared pool leaves
        no key on disk.
        """
        units = self.units()
        if not units and not purge:
            return False

        providers: List[Dict[str, Any]] = []
        engines: List[Dict[str, Any]] = []
        per_label: Dict[str, Dict[str, Any]] = {}
        for unit in units:
            provider = per_label.get(unit.label)
            if provider is None:
                provider = {
                    "id": unit.provider_id,
                    "kind": "openai",
                    "base_url": ctx.config.translator.gemini_base_url,
                    "api_key": unit.key,
                    "rpm": unit.rpm,
                    "max_concurrency": 2,
                }
                per_label[unit.label] = provider
                providers.append(provider)
            engines.append(
                {
                    "id": unit.engine_id,
                    "provider": unit.provider_id,
                    "model": unit.model,
                    "max_input_tokens": ctx.config.translator.max_request_tokens,
                }
            )

        # A keyless lane for the glossary pre-pass and for an explicit Bing call;
        # the pool's own AI lane never falls back to it.
        providers.append(
            {
                "id": "bing",
                "kind": "bing",
                "requires_key": False,
                "rps": 3,
                "max_concurrency": 4,
            }
        )
        engines.append({"id": "bing", "provider": "bing"})

        # No `routing` block: every call is pinned to one engine through
        # `Router.run(task, override=...)`, so a lane here would only describe a
        # fallback path that is never taken.
        config = {
            "providers": providers,
            "engines": engines,
            "failure_policy": {
                "transient_retries": 1,
                "backoff_base_seconds": 2.0,
                "failure_threshold": 3,
                "cooldown_seconds": 30.0,
            },
        }
        text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        path = ctx.config.translator.config_file
        try:
            with open(path, encoding="utf-8") as handle:
                if handle.read() == text:
                    return False
        except FileNotFoundError:
            pass
        except Exception:
            pass
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{uuid4().hex[:6]}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
            logger.info(f"Key pool: wrote translator config for {len(units)} units")
            return True
        except Exception:
            logger.warning("Unable to write the translator config", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Direct model calls (glossary tasks)
    # ------------------------------------------------------------------

    def generate(
        self,
        task: str,
        prompt: str,
        *,
        signal: Any = None,
        timeout: int = 180,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """Run one prompt on the pool, returning `(text, model)`.

        Glossary translation and classification are plain prompt jobs, exactly
        like the notebook's batched glossary cell, so they go straight to the
        key's OpenAI-compatible endpoint instead of through the translator
        package's HTML pipeline.
        """
        estimated = max(1, len(prompt) // 4)
        # The request layer already rejects an unknown pin; repeating the call
        # here keeps a direct caller from turning that typo into "no key could
        # answer", without a second copy of the rule.
        self.pin(model)
        pool = self.units(model)
        if not pool:
            return None
        tried: List[str] = []
        while len(tried) < len(pool):
            lease = self.lease(
                task,
                estimated_tokens=estimated,
                exclude=tried,
                units=pool,
                model=model,
            )
            if lease is None:
                return None
            tried.append(lease.unit.id)
            unit = lease.unit
            url = f"{ctx.config.translator.gemini_base_url.rstrip('/')}/chat/completions"
            try:
                response = ctx.http.post(
                    url,
                    signal=signal,
                    json={
                        "model": unit.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                    },
                    headers={
                        "Authorization": f"Bearer {unit.key}",
                        "Content-Type": "application/json",
                    },
                    timeout=timeout,
                )
            except Exception as error:
                self.report(lease, error="failed", detail=str(error))
                continue

            if response.status_code == 429:
                self.report(lease, error="rate", detail="429")
                continue
            if response.status_code >= 400:
                kind = "quota" if response.status_code in (403, 503) else "failed"
                detail = response.text[:200]
                self.report(lease, error=kind, detail=detail)
                continue
            try:
                data = response.json()
                text = str(data["choices"][0]["message"]["content"] or "")
            except Exception as error:
                self.report(lease, error="failed", detail=f"unreadable response: {error}")
                continue

            usage = data.get("usage") or {}
            self.report(
                lease,
                tokens_in=int(usage.get("prompt_tokens") or estimated),
                tokens_out=int(usage.get("completion_tokens") or max(1, len(text) // 4)),
            )
            return text, unit.model
        return None
