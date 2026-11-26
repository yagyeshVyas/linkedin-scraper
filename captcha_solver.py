"""External CAPTCHA solving — the tier that removes the human from the loop.

The browser tiers in :mod:`cf_bypass` can wait out a challenge, click a
checkbox and replay a clearance cookie.  What they cannot do is *answer* an
interactive Turnstile / reCAPTCHA / hCaptcha that is deliberately gated on a
human.  This module closes that gap by handing the challenge to a solving
service over HTTP and returning the token the widget would have produced.

Supported providers (all speak plain HTTPS JSON, no SDK required):

    capsolver      https://api.capsolver.com      fastest Turnstile support
    2captcha       https://2captcha.com           widest coverage
    anticaptcha    https://api.anti-captcha.com   solid reCAPTCHA/hCaptcha

Import-safe by design: nothing here opens a socket, imports a browser or reads
the environment at import time.  A missing key means ``configured`` is False
and every caller degrades to the old behaviour instead of crashing.

Public surface:

    CaptchaSolver(provider, api_key, ...)      -> solver
    default_solver()                           -> solver from env/config
    solver.solve_turnstile(sitekey, url, ...)  -> token ("" on failure)
    solver.solve_recaptcha(sitekey, url, ...)  -> token
    solver.solve_hcaptcha(sitekey, url, ...)   -> token
    solver.balance()                           -> float | None
    solver_report()                            -> dict for doctor
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Provider name -> (create endpoint, result endpoint, balance endpoint).
_ENDPOINTS = {
    "capsolver": (
        "https://api.capsolver.com/createTask",
        "https://api.capsolver.com/getTaskResult",
        "https://api.capsolver.com/getBalance",
    ),
    "anticaptcha": (
        "https://api.anti-captcha.com/createTask",
        "https://api.anti-captcha.com/getTaskResult",
        "https://api.anti-captcha.com/getBalance",
    ),
    "2captcha": (
        "https://2captcha.com/in.php",
        "https://2captcha.com/res.php",
        "https://2captcha.com/res.php",
    ),
}

#: Environment variables checked, in order, when no key is passed explicitly.
_ENV_KEYS = (
    ("capsolver", "CAPSOLVER_API_KEY"),
    ("2captcha", "TWOCAPTCHA_API_KEY"),
    ("2captcha", "CAPTCHA_2CAPTCHA_KEY"),
    ("anticaptcha", "ANTICAPTCHA_API_KEY"),
)

#: Task-type names per provider and challenge family.
_TASK_TYPES = {
    "capsolver": {
        "turnstile": "AntiTurnstileTaskProxyLess",
        "recaptcha": "ReCaptchaV2TaskProxyLess",
        "hcaptcha": "HCaptchaTaskProxyLess",
    },
    "anticaptcha": {
        "turnstile": "TurnstileTaskProxyless",
        "recaptcha": "RecaptchaV2TaskProxyless",
        "hcaptcha": "HCaptchaTaskProxyless",
    },
    "2captcha": {
        "turnstile": "turnstile",
        "recaptcha": "userrecaptcha",
        "hcaptcha": "hcaptcha",
    },
}

#: Terminal solver states that are worth retrying on a fresh task.
_TRANSIENT = (
    "ERROR_CAPTCHA_UNSOLVABLE",
    "ERROR_NO_SLOT_AVAILABLE",
    "ERROR_SERVICE_OVERLOAD",
    "ERROR_EMPTY_ACTION",
    "ERROR_TASK_TIMEOUT",
    "ERROR_NETWORK",
)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class CaptchaError(RuntimeError):
    """A provider refused the task or answered with a terminal error."""


def _post_json(url: str, payload: dict, timeout: float,
               headers: Optional[Dict[str, str]] = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _DEFAULT_UA, **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise CaptchaError(f"non-JSON response from {url}: {raw[:160]!r}") from exc
    if not isinstance(parsed, dict):
        raise CaptchaError(f"unexpected response shape from {url}")
    return parsed


def _post_form(url: str, fields: Dict[str, str], timeout: float) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json", "User-Agent": _DEFAULT_UA},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except ValueError:
        # 2captcha can answer with the bare "OK|<token>" form.
        return {"raw": raw.strip()}
    return parsed if isinstance(parsed, dict) else {"raw": raw.strip()}


class CaptchaSolver:
    """One solving service, normalised behind three ``solve_*`` calls."""

    def __init__(self, provider: str = "", api_key: str = "", *,
                 timeout: float = 120.0, poll: float = 3.0,
                 retries: int = 1, proxy: str = "", service_url: str = "",
                 user_agent: str = _DEFAULT_UA):
        self.provider = str(provider or "").strip().lower()
        self.api_key = str(api_key or "").strip()
        self.timeout = max(10.0, float(timeout))
        self.poll = max(1.0, float(poll))
        self.retries = max(0, int(retries))
        self.proxy = str(proxy or "").strip()
        self.service_url = str(service_url or "").rstrip("/")
        self.user_agent = user_agent or _DEFAULT_UA

    # ── configuration ──

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.api_key
                    and self.provider in _ENDPOINTS)

    @property
    def _endpoints(self) -> Tuple[str, str, str]:
        create, result, balance = _ENDPOINTS[self.provider]
        if self.service_url:
            create = f"{self.service_url}/createTask"
            result = f"{self.service_url}/getTaskResult"
            balance = f"{self.service_url}/getBalance"
        return create, result, balance

    def describe(self) -> str:
        if not self.configured:
            return "captcha solver: not configured"
        return f"captcha solver: {self.provider} ({self.api_key[:4]}…{self.api_key[-2:]})"

    # ── public solvers ──

    def solve_turnstile(self, sitekey: str, page_url: str, *,
                        action: str = "", cdata: str = "") -> str:
        return self._solve("turnstile", sitekey, page_url,
                           {"action": action, "cdata": cdata})

    def solve_recaptcha(self, sitekey: str, page_url: str, *,
                        invisible: bool = False) -> str:
        return self._solve("recaptcha", sitekey, page_url,
                           {"invisible": "1" if invisible else ""})

    def solve_hcaptcha(self, sitekey: str, page_url: str, *,
                       invisible: bool = False) -> str:
        return self._solve("hcaptcha", sitekey, page_url,
                           {"invisible": "1" if invisible else ""})

    def solve(self, kind: str, sitekey: str, page_url: str, **kwargs) -> str:
        """Dispatch by family name: ``turnstile`` / ``recaptcha`` / ``hcaptcha``."""
        return self._solve(str(kind or "").lower(), sitekey, page_url, kwargs)

    # ── core ──

    def _solve(self, family: str, sitekey: str, page_url: str,
               extra: Dict[str, Any]) -> str:
        if not self.configured:
            logger.debug("captcha solver not configured; skipping %s", family)
            return ""
        if not sitekey or not page_url:
            logger.warning("captcha solve skipped: missing sitekey or url")
            return ""
        if family not in _TASK_TYPES[self.provider]:
            logger.warning("captcha family %r unsupported by %s", family, self.provider)
            return ""

        attempts = self.retries + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                task_id = self._create_task(family, sitekey, page_url, extra)
                token = self._await_token(task_id, family)
                if token:
                    logger.info("captcha solved: %s via %s", family, self.provider)
                    return token
                last_error = "empty token"
            except CaptchaError as exc:
                last_error = str(exc)
                logger.warning("captcha attempt %d/%d failed: %s",
                               attempt + 1, attempts, last_error)
                if not any(code in last_error for code in _TRANSIENT):
                    break
            except Exception as exc:  # network, DNS, TLS, timeout
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("captcha transport error: %s", last_error)
            if attempt + 1 < attempts:
                time.sleep(min(8.0, 2.0 * (attempt + 1)))
        logger.error("captcha unsolved (%s): %s", family, last_error or "unknown")
        return ""

    def _create_task(self, family: str, sitekey: str, page_url: str,
                     extra: Dict[str, Any]) -> str:
        if self.provider == "2captcha":
            return self._create_2captcha(family, sitekey, page_url, extra)
        return self._create_json(family, sitekey, page_url, extra)

    def _create_json(self, family: str, sitekey: str, page_url: str,
                     extra: Dict[str, Any]) -> str:
        create_url, _, _ = self._endpoints
        task: Dict[str, Any] = {
            "type": _TASK_TYPES[self.provider][family],
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        if family == "turnstile":
            meta = {}
            if extra.get("action"):
                meta["action"] = extra["action"]
            if extra.get("cdata"):
                meta["cdata"] = extra["cdata"]
            if meta:
                task["metadata"] = meta
        elif extra.get("invisible"):
            task["isInvisible"] = True
        payload: Dict[str, Any] = {"clientKey": self.api_key, "task": task}
        if self.proxy:
            payload["proxy"] = self.proxy
        body = _post_json(create_url, payload, self.timeout)
        error_id = int(body.get("errorId") or 0)
        if error_id:
            raise CaptchaError(str(body.get("errorCode") or body.get("errorDescription")
                                   or f"createTask error {error_id}"))
        task_id = str(body.get("taskId") or "")
        if not task_id:
            raise CaptchaError("createTask returned no taskId")
        return task_id

    def _create_2captcha(self, family: str, sitekey: str, page_url: str,
                         extra: Dict[str, Any]) -> str:
        create_url, _, _ = self._endpoints
        fields = {
            "key": self.api_key,
            "method": _TASK_TYPES["2captcha"][family],
            "sitekey": sitekey,
            "pageurl": page_url,
            "json": "1",
        }
        if family == "turnstile":
            if extra.get("action"):
                fields["action"] = extra["action"]
            if extra.get("cdata"):
                fields["data"] = extra["cdata"]
        elif extra.get("invisible"):
            fields["invisible"] = "1"
        if self.proxy:
            fields["proxy"] = self.proxy
            fields["proxytype"] = "HTTP"
        body = _post_form(create_url, fields, self.timeout)
        status = str(body.get("status") or "")
        if status != "1":
            raise CaptchaError(str(body.get("request") or "2captcha rejected the task"))
        task_id = str(body.get("request") or "")
        if not task_id:
            raise CaptchaError("2captcha returned no task id")
        return task_id

    def _await_token(self, task_id: str, family: str) -> str:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll)
            if self.provider == "2captcha":
                token, state = self._poll_2captcha(task_id)
            else:
                token, state = self._poll_json(task_id)
            if token:
                return token
            if state in _TRANSIENT:
                raise CaptchaError(state)
            if state and state not in ("processing", "ready", ""):
                raise CaptchaError(state)
        return ""

    def _poll_json(self, task_id: str) -> Tuple[str, str]:
        _, result_url, _ = self._endpoints
        body = _post_json(result_url, {"clientKey": self.api_key, "taskId": task_id},
                          min(self.timeout, 30.0))
        error_id = int(body.get("errorId") or 0)
        if error_id:
            raise CaptchaError(str(body.get("errorCode") or f"getTaskResult error {error_id}"))
        status = str(body.get("status") or "").lower()
        if status == "ready":
            solution = body.get("solution") or {}
            token = str(solution.get("token") or solution.get("gRecaptchaResponse")
                        or solution.get("answer") or "")
            return token, "ready"
        return "", status

    def _poll_2captcha(self, task_id: str) -> Tuple[str, str]:
        _, result_url, _ = self._endpoints
        query = urllib.parse.urlencode({
            "key": self.api_key, "action": "get", "id": task_id, "json": "1",
        })
        url = f"{result_url}?{query}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=min(self.timeout, 30.0)) as response:
            raw = response.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw.strip()}
        status = str(body.get("status") or "")
        answer = str(body.get("request") or body.get("raw") or "")
        if status == "1" or answer.startswith("OK|"):
            return answer.split("|", 1)[-1].strip(), "ready"
        if answer in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
            return "", "processing"
        return "", answer or "error"

    # ── diagnostics ──

    def balance(self) -> Optional[float]:
        if not self.configured:
            return None
        try:
            if self.provider == "2captcha":
                _, result_url, _ = self._endpoints
                query = urllib.parse.urlencode({
                    "key": self.api_key, "action": "getbalance", "json": "1"})
                request = urllib.request.Request(
                    f"{result_url}?{query}",
                    headers={"Accept": "application/json", "User-Agent": self.user_agent})
                with urllib.request.urlopen(request, timeout=15.0) as response:
                    body = json.loads(response.read().decode("utf-8", "replace"))
                return float(body.get("request") or 0) or None
            _, _, balance_url = self._endpoints
            body = _post_json(balance_url, {"clientKey": self.api_key}, 15.0)
            if int(body.get("errorId") or 0):
                return None
            return float(body.get("balance") or 0) or None
        except Exception as exc:
            logger.debug("balance check failed: %s", exc)
            return None


def solver_from_env() -> CaptchaSolver:
    """Build a solver from the environment (no config import, no side effects)."""
    for provider, env_name in _ENV_KEYS:
        value = os.environ.get(env_name, "").strip()
        if value:
            return CaptchaSolver(provider, value)
    return CaptchaSolver()


_DEFAULT: Optional[CaptchaSolver] = None
_DEFAULT_LOCK = False


def default_solver(refresh: bool = False) -> CaptchaSolver:
    """Process-wide solver, resolved once from config/env.

    Config wins over the environment so a dashboard/CLI setting is explicit,
    and every lookup is guarded: a missing ``config`` module is normal in unit
    tests and must not break the import.
    """
    global _DEFAULT, _DEFAULT_LOCK
    if _DEFAULT is not None and not refresh:
        return _DEFAULT
    if _DEFAULT_LOCK and not refresh:
        return _DEFAULT or CaptchaSolver()

    provider = ""
    api_key = ""
    timeout = 120.0
    poll = 3.0
    retries = 1
    service_url = ""
    proxy = ""
    try:
        from config import Config  # local import keeps the module import-safe
        provider = str(getattr(Config, "CF_CAPTCHA_PROVIDER", "") or "")
        api_key = str(getattr(Config, "CF_CAPTCHA_API_KEY", "") or "")
        timeout = float(getattr(Config, "CF_CAPTCHA_SOLVE_TIMEOUT", 120) or 120)
        poll = float(getattr(Config, "CF_CAPTCHA_POLL_SECONDS", 3) or 3)
        retries = int(getattr(Config, "CF_CAPTCHA_RETRIES", 1) or 0)
        service_url = str(getattr(Config, "CF_CAPTCHA_SERVICE_URL", "") or "")
        proxy = str(getattr(Config, "CF_CAPTCHA_PROXY", "") or "")
    except Exception:
        pass

    if not api_key:
        env_solver = solver_from_env()
        if env_solver.configured:
            env_solver.timeout = timeout
            env_solver.poll = poll
            env_solver.retries = retries
            _DEFAULT = env_solver
            _DEFAULT_LOCK = True
            return _DEFAULT

    _DEFAULT = CaptchaSolver(provider, api_key, timeout=timeout, poll=poll,
                             retries=retries, service_url=service_url, proxy=proxy)
    _DEFAULT_LOCK = True
    return _DEFAULT


def solver_report() -> dict:
    """Shape used by ``cli.py doctor`` — never raises."""
    solver = default_solver()
    report: Dict[str, Any] = {
        "configured": solver.configured,
        "provider": solver.provider or "",
        "describe": solver.describe(),
        "balance": None,
    }
    if solver.configured:
        report["balance"] = solver.balance()
    return report


#: Families the browser layer can hand over, in the order it tries them.
SUPPORTED_FAMILIES: List[str] = ["turnstile", "recaptcha", "hcaptcha"]
