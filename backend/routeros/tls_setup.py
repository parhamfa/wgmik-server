"""Automated TLS provisioning on RouterOS for plaintext connection profiles.

Flow (driven from the API layer):
  1. start_tls_setup() spawns a background job that, over the existing
     plaintext channel (rest-http or api-plain):
       - checks access,
       - creates+signs a self-signed cert (or runs built-in ACME for
         Let's Encrypt),
       - binds it to the matching TLS service (www-ssl / api-ssl),
       - verifies a fresh TLS connection end-to-end.
  2. get_job() is polled for step-by-step progress.
  3. After the job succeeds, the API layer flips the router profile and may
     call disable_plain_service() over the now-TLS channel.

Setup-only operations live here on purpose; RouterOSClient stays WG-focused.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, Dict, List, Optional

import httpx
from librouteros import connect

from .rest_client import RouterOSRestClient
from .api_client import RouterOSApiClient


class TlsSetupError(RuntimeError):
    pass


# proto of the plaintext profile -> TLS target description
TLS_TARGETS = {
    "rest-http": {"service": "www-ssl", "plain_service": "www", "proto": "rest", "default_port": 443},
    "api-plain": {"service": "api-ssl", "plain_service": "api", "proto": "api", "default_port": 8729},
}

CERT_NAME_PREFIX = "wgmik-"
SIGN_POLL_SECONDS = 60
ACME_TIMEOUT_SECONDS = 150


def _sanitize_cert_name(host: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", host.strip()) or "router"
    return f"{CERT_NAME_PREFIX}{cleaned}"[:64]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes")


# ── Transports ───────────────────────────────────────────────────────────


class _RestTransport:
    """Minimal raw REST access (any scheme) for setup commands."""

    def __init__(self, host: str, port: int, username: str, password: str, *, https: bool, verify: bool, timeout: float = 25.0):
        scheme = "https" if https else "http"
        self._base = f"{scheme}://{host}:{port}/rest"
        self._auth = (username, password)
        self._verify = verify if https else True
        self._timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[dict] = None, timeout: Optional[float] = None):
        with httpx.Client(verify=self._verify, timeout=timeout or self._timeout, auth=self._auth) as c:
            r = c.request(method, f"{self._base}{path}", json=payload)
            if r.status_code >= 400:
                raise TlsSetupError(f"RouterOS {path} failed: HTTP {r.status_code} {r.text[:300]}")
            if r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            return r.text

    def print_rows(self, menu: str) -> List[dict]:
        data = self._request("GET", menu)
        return data if isinstance(data, list) else []

    def command(self, path: str, params: dict, timeout: Optional[float] = None):
        return self._request("POST", path, payload=params, timeout=timeout)

    def close(self) -> None:
        pass


class _ApiTransport:
    """Minimal raw binary-API access for setup commands."""

    def __init__(self, host: str, port: int, username: str, password: str, *, use_tls: bool, ssl_verify: bool, timeout: int = 25):
        self._api = connect(
            username=username,
            password=password,
            host=host,
            port=port,
            use_ssl=use_tls,
            ssl_verify=ssl_verify if use_tls else False,
            timeout=timeout,
        )

    def print_rows(self, menu: str) -> List[dict]:
        rows = self._api(cmd=f"{menu}/print")
        return [dict(r) for r in rows or []]

    def command(self, path: str, params: dict, timeout: Optional[float] = None):
        res = self._api(cmd=path, **params)
        try:
            return [dict(r) for r in res or []]
        except TypeError:
            return res

    def close(self) -> None:
        try:
            self._api.close()
        except Exception:
            pass


def _make_transport(proto: str, host: str, port: int, username: str, password: str):
    if proto in ("rest", "rest-http"):
        return _RestTransport(host, port, username, password, https=(proto == "rest"), verify=False)
    return _ApiTransport(host, port, username, password, use_tls=(proto == "api"), ssl_verify=False)


def _command_with_id(transport, path: str, ros_id: str, extra: dict, timeout: Optional[float] = None):
    """RouterOS accepts '.id' on API; REST builds variously want 'number'/'numbers'."""
    first_err: Optional[Exception] = None
    for key in (".id", "number", "numbers"):
        try:
            return transport.command(path, {key: ros_id, **extra}, timeout=timeout)
        except Exception as exc:
            if first_err is None:
                first_err = exc
    raise first_err  # type: ignore[misc]


# ── Job model ────────────────────────────────────────────────────────────


STEP_IDS = ("check", "certificate", "service", "verify")
STEP_LABELS = {
    "check": "Checking router access",
    "certificate": "Provisioning certificate",
    "service": "Enabling TLS service",
    "verify": "Verifying TLS connection",
}


@dataclass
class TlsSetupStep:
    id: str
    label: str
    status: str = "pending"  # pending | running | ok | failed
    detail: str = ""


@dataclass
class TlsSetupJob:
    router_id: int
    method: str  # self_signed | letsencrypt
    status: str = "running"  # running | ok | failed
    error: str = ""
    steps: List[TlsSetupStep] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "router_id": self.router_id,
            "method": self.method,
            "status": self.status,
            "error": self.error,
            "steps": [{"id": s.id, "label": s.label, "status": s.status, "detail": s.detail} for s in self.steps],
            "result": dict(self.result),
        }


_jobs: Dict[int, TlsSetupJob] = {}
_jobs_lock = threading.Lock()


def get_job(router_id: int) -> Optional[dict]:
    with _jobs_lock:
        job = _jobs.get(router_id)
        return job.snapshot() if job else None


def clear_job(router_id: int) -> None:
    with _jobs_lock:
        _jobs.pop(router_id, None)


# ── RouterOS operations ──────────────────────────────────────────────────


def _find_cert(transport, name: str) -> Optional[dict]:
    for row in transport.print_rows("/certificate"):
        if (row.get("name") or "") == name:
            return row
    return None


def _cert_usable(row: Optional[dict]) -> bool:
    if not row:
        return False
    if not _truthy(row.get("private-key")):
        return False
    if not (row.get("fingerprint") or "").strip():
        return False
    if _truthy(row.get("expired")) or _truthy(row.get("invalid")):
        return False
    return True


def _subject_alt_name(common_name: str) -> str:
    try:
        ip_address(common_name)
        return f"IP:{common_name}"
    except ValueError:
        return f"DNS:{common_name}"


def _add_cert(transport, params: dict) -> None:
    try:
        transport.command("/certificate/add", dict(params))
    except Exception:
        # Older builds may reject subject-alt-name on add.
        if "subject-alt-name" not in params:
            raise
        slim = dict(params)
        slim.pop("subject-alt-name", None)
        transport.command("/certificate/add", slim)


def _sign_and_wait(transport, name: str, extra: dict, step: TlsSetupStep) -> dict:
    row = _find_cert(transport, name)
    if not row:
        raise TlsSetupError(f"certificate '{name}' was not created")
    _command_with_id(transport, "/certificate/sign", row.get(".id") or name, extra, timeout=60.0)
    deadline = time.monotonic() + SIGN_POLL_SECONDS
    while time.monotonic() < deadline:
        row = _find_cert(transport, name)
        if _cert_usable(row):
            return row
        time.sleep(1.0)
    raise TlsSetupError(f"certificate '{name}' did not finish signing within {SIGN_POLL_SECONDS}s")


def _ensure_local_ca(transport, days_valid: int, step: TlsSetupStep) -> dict:
    """Newer RouterOS (7.21+) refuses to self-sign a tls-server template
    ('CA not found'), so keep a small router-local CA to sign with."""
    name = f"{CERT_NAME_PREFIX}ca"
    existing = _find_cert(transport, name)
    if _cert_usable(existing):
        return existing
    if existing is not None:
        _command_with_id(transport, "/certificate/remove", existing.get(".id") or name, {})
    _add_cert(transport, {
        "name": name,
        "common-name": "wgmik local CA",
        "key-size": "2048",
        "days-valid": str(int(days_valid)),
        "key-usage": "key-cert-sign,crl-sign",
    })
    step.detail = f"Creating local CA '{name}'..."
    return _sign_and_wait(transport, name, {}, step)


def _ensure_self_signed_cert(transport, host: str, common_name: str, days_valid: int, step: TlsSetupStep) -> dict:
    name = _sanitize_cert_name(host)
    existing = _find_cert(transport, name)
    if _cert_usable(existing):
        step.detail = f"Reusing existing certificate '{name}'"
        return existing

    if existing is not None:
        # Our own stale/unsigned leftover; safe to replace.
        _command_with_id(transport, "/certificate/remove", existing.get(".id") or name, {})

    ca = _ensure_local_ca(transport, days_valid, step)
    ca_name = str(ca.get("name") or f"{CERT_NAME_PREFIX}ca")

    _add_cert(transport, {
        "name": name,
        "common-name": common_name,
        "key-size": "2048",
        "days-valid": str(int(days_valid)),
        "key-usage": "digital-signature,key-encipherment,tls-server",
        "subject-alt-name": _subject_alt_name(common_name),
    })

    step.detail = f"Signing certificate '{name}' with '{ca_name}'..."
    row = _sign_and_wait(transport, name, {"ca": ca_name}, step)
    step.detail = f"Certificate '{name}' signed by '{ca_name}'"
    return row


def _recent_acme_log_lines(transport, dns_name: str, max_lines: int = 8) -> List[str]:
    """Best-effort: pull ACME-related lines from the router system log."""
    try:
        rows = transport.print_rows("/log")
    except Exception:
        return []
    needle = dns_name.strip().lower()
    hits: List[str] = []
    for row in rows[-300:]:
        msg = str(row.get("message") or "").strip()
        topics = str(row.get("topics") or "").strip()
        haystack = f"{topics} {msg}".lower()
        if not msg:
            continue
        if "acme" in haystack or "letsencrypt" in haystack or "ssl-certificate" in haystack or (needle and needle in haystack):
            hits.append(f"[{row.get('time', '')}] {msg}")
    return hits[-max_lines:]


def _summarize_response(res: Any) -> str:
    if res in (None, "", [], {}):
        return ""
    text = str(res)
    return text[:400]


def _run_letsencrypt(transport, dns_name: str, step: TlsSetupStep) -> dict:
    step.detail = "Requesting Let's Encrypt certificate (router needs public TCP/80)..."
    response_summary = ""
    try:
        res = transport.command("/certificate/enable-ssl-certificate", {"dns-name": dns_name}, timeout=float(ACME_TIMEOUT_SECONDS))
        response_summary = _summarize_response(res)
    except Exception as exc:
        log_lines = _recent_acme_log_lines(transport, dns_name)
        detail = f"Let's Encrypt request failed: {exc}"
        if log_lines:
            detail += "\nRouter log:\n" + "\n".join(log_lines)
        raise TlsSetupError(detail) from exc

    # Find the freshest usable cert matching the DNS name.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        candidates = [
            row for row in transport.print_rows("/certificate")
            if (row.get("common-name") or "").strip().lower() == dns_name.strip().lower() and _cert_usable(row)
        ]
        if candidates:
            best = candidates[-1]
            step.detail = f"Let's Encrypt certificate '{best.get('name')}' issued"
            return best
        time.sleep(2.0)

    log_lines = _recent_acme_log_lines(transport, dns_name)
    parts = [f"Let's Encrypt did not produce a usable certificate for '{dns_name}'."]
    if response_summary:
        parts.append(f"Router response: {response_summary}")
    if log_lines:
        parts.append("Router log:\n" + "\n".join(log_lines))
    else:
        parts.append("No ACME entries found in the router log.")
    parts.append(
        "The www service was made available on port 80 for the challenge, so remaining causes are: "
        "the DNS name does not resolve to this router's public IP, "
        "a firewall filter/NAT rule blocks TCP/80 from the internet (this app does not modify firewalls), "
        "or the router has no public IP."
    )
    raise TlsSetupError("\n".join(parts))


def _service_row(transport, name: str) -> Optional[dict]:
    rows = transport.print_rows("/ip/service")
    return next((r for r in rows if (r.get("name") or "") == name), None)


def _set_service(transport, row: dict, params: dict) -> None:
    _command_with_id(transport, "/ip/service/set", row.get(".id") or str(row.get("name") or ""), params)


def _run_letsencrypt_with_www_prep(transport, args: dict, step: TlsSetupStep):
    """RouterOS built-in ACME only does HTTP-01, which needs the www service on
    port 80. Temporarily arrange that, run ACME, then restore the service.
    Returns (cert_row, transport) — the transport may be replaced when the
    control channel itself rides on the www service (rest-http profiles)."""
    row = _service_row(transport, "www")
    if not row:
        raise TlsSetupError("www service not found on router")
    try:
        orig_port = int(str(row.get("port") or "80").strip() or "80")
    except ValueError:
        orig_port = 80
    orig_disabled = _truthy(row.get("disabled"))
    needs_change = orig_port != 80 or orig_disabled
    on_www_channel = args["proto"] == "rest-http"

    if needs_change:
        state = "disabled" if orig_disabled else f"on port {orig_port}"
        step.detail = f"Temporarily moving www service ({state}) to port 80 for the ACME challenge..."
        _set_service(transport, row, {"port": "80", "disabled": "no"})
        if on_www_channel:
            # Our own control channel was the www service; follow it to port 80.
            transport.close()
            transport = _RestTransport(args["host"], 80, args["username"], args["password"], https=False, verify=True)

    acme_error: Optional[Exception] = None
    cert: Optional[dict] = None
    try:
        cert = _run_letsencrypt(transport, args["dns_name"], step)
    except Exception as exc:
        acme_error = exc

    if needs_change:
        try:
            row_now = _service_row(transport, "www") or row
            _set_service(transport, row_now, {"port": str(orig_port), "disabled": "yes" if orig_disabled else "no"})
        except Exception:
            # For rest-http the port flip can cut our in-flight response even
            # though the change applied; verify over the original channel below.
            pass
        if on_www_channel:
            transport.close()
            transport = _RestTransport(args["host"], args["port"], args["username"], args["password"], https=False, verify=True)
        restored_row = _service_row(transport, "www")
        restored_port = str((restored_row or {}).get("port") or "")
        if not restored_row or restored_port != str(orig_port) or _truthy(restored_row.get("disabled")) != orig_disabled:
            note = f"WARNING: could not confirm the www service was restored to its original state (port {orig_port}, {'disabled' if orig_disabled else 'enabled'}); check /ip/service on the router."
            if acme_error is not None:
                acme_error = TlsSetupError(f"{acme_error}\n{note}")
            else:
                step.detail = f"{step.detail}\n{note}"
        elif not acme_error:
            step.detail = f"{step.detail}\nwww service restored ({'disabled' if orig_disabled else f'port {orig_port}'})."

    if acme_error is not None:
        raise acme_error
    return cert, transport


def _bind_service(transport, service_name: str, cert_name: str, step: TlsSetupStep) -> int:
    rows = transport.print_rows("/ip/service")
    row = next((r for r in rows if (r.get("name") or "") == service_name), None)
    if not row:
        raise TlsSetupError(f"service '{service_name}' not found on router")
    _command_with_id(
        transport,
        "/ip/service/set",
        row.get(".id") or service_name,
        {"certificate": cert_name, "disabled": "no"},
    )
    rows = transport.print_rows("/ip/service")
    row = next((r for r in rows if (r.get("name") or "") == service_name), None) or row
    try:
        port = int(str(row.get("port") or "").strip())
    except ValueError:
        port = 0
    if port <= 0:
        port = next(t["default_port"] for t in TLS_TARGETS.values() if t["service"] == service_name)
    step.detail = f"Service '{service_name}' enabled on port {port} with certificate '{cert_name}'"
    return port


def _verify_tls(proto: str, host: str, port: int, username: str, password: str, tls_verify: bool) -> str:
    if proto == "rest":
        client = RouterOSRestClient(
            host=host,
            port=port,
            username=username,
            password=password,
            tls_verify=tls_verify,
            https=True,
            allow_scheme_fallback=False,
            timeout=12.0,
        )
    else:
        client = RouterOSApiClient(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=True,
            ssl_verify=tls_verify,
            timeout=12,
        )
    version = (client.get_system_version() or "").strip()
    if not version:
        raise TlsSetupError("TLS connection succeeded but RouterOS returned no version")
    return version


def verify_target(result: dict, username: str, password: str) -> str:
    """Re-verify the TLS target described by a finished job result."""
    return _verify_tls(
        proto=str(result.get("proto") or ""),
        host=str(result.get("host") or ""),
        port=int(result.get("port") or 0),
        username=username,
        password=password,
        tls_verify=bool(result.get("tls_verify")),
    )


def disable_plain_service(*, proto: str, host: str, port: int, username: str, password: str, tls_verify: bool, plain_service: str) -> None:
    """Disable the plaintext RouterOS service, over the (now TLS) profile."""
    if proto == "api":
        transport = _ApiTransport(host, port, username, password, use_tls=True, ssl_verify=tls_verify)
    else:
        transport = _RestTransport(host, port, username, password, https=True, verify=tls_verify)
    try:
        rows = transport.print_rows("/ip/service")
        row = next((r for r in rows if (r.get("name") or "") == plain_service), None)
        if not row:
            raise TlsSetupError(f"service '{plain_service}' not found on router")
        if _truthy(row.get("disabled")):
            return
        _command_with_id(transport, "/ip/service/set", row.get(".id") or plain_service, {"disabled": "yes"})
    finally:
        transport.close()


# ── Job orchestration ────────────────────────────────────────────────────


def start_tls_setup(
    *,
    router_id: int,
    proto: str,
    host: str,
    port: int,
    username: str,
    password: str,
    method: str,
    common_name: str = "",
    days_valid: int = 3650,
    dns_name: str = "",
) -> dict:
    if proto not in TLS_TARGETS:
        raise TlsSetupError(f"profile proto '{proto}' is already TLS or unsupported")
    if method not in ("self_signed", "letsencrypt"):
        raise TlsSetupError("method must be 'self_signed' or 'letsencrypt'")
    if method == "letsencrypt" and not dns_name.strip():
        raise TlsSetupError("dns_name is required for Let's Encrypt")

    with _jobs_lock:
        existing = _jobs.get(router_id)
        if existing and existing.status == "running":
            raise TlsSetupError("a TLS setup is already running for this router")
        job = TlsSetupJob(
            router_id=router_id,
            method=method,
            steps=[TlsSetupStep(id=sid, label=STEP_LABELS[sid]) for sid in STEP_IDS],
        )
        _jobs[router_id] = job

    args = {
        "proto": proto,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "common_name": (common_name or host).strip(),
        "days_valid": max(1, int(days_valid)),
        "dns_name": dns_name.strip(),
    }
    thread = threading.Thread(target=_run_job, args=(job, args), daemon=True, name=f"tls-setup-{router_id}")
    thread.start()
    return job.snapshot()


def _run_job(job: TlsSetupJob, args: dict) -> None:
    target = TLS_TARGETS[args["proto"]]
    steps = {s.id: s for s in job.steps}
    transport = None
    current: Optional[TlsSetupStep] = None
    try:
        current = steps["check"]
        current.status = "running"
        transport = _make_transport(args["proto"], args["host"], args["port"], args["username"], args["password"])
        rows = transport.print_rows("/system/resource")
        version = str((rows[0] if rows else {}).get("version") or "").strip()
        current.detail = f"Connected (RouterOS {version})" if version else "Connected"
        current.status = "ok"

        current = steps["certificate"]
        current.status = "running"
        if job.method == "self_signed":
            cert = _ensure_self_signed_cert(transport, args["host"], args["common_name"], args["days_valid"], current)
        else:
            cert, transport = _run_letsencrypt_with_www_prep(transport, args, current)
        cert_name = str(cert.get("name") or "")
        current.status = "ok"

        current = steps["service"]
        current.status = "running"
        tls_port = _bind_service(transport, target["service"], cert_name, current)
        current.status = "ok"

        current = steps["verify"]
        current.status = "running"
        tls_verify = job.method == "letsencrypt"
        verify_host = args["dns_name"] if job.method == "letsencrypt" else args["host"]
        version = _verify_tls(target["proto"], verify_host, tls_port, args["username"], args["password"], tls_verify)
        current.detail = f"TLS connection OK (RouterOS {version})"
        current.status = "ok"

        job.result = {
            "proto": target["proto"],
            "host": verify_host,
            "port": tls_port,
            "tls_verify": tls_verify,
            "service": target["service"],
            "plain_service": target["plain_service"],
            "cert_name": cert_name,
            "fingerprint": str(cert.get("fingerprint") or ""),
            "expires_after": str(cert.get("invalid-after") or ""),
            "ros_version": version,
        }
        job.status = "ok"
    except Exception as exc:  # surface RouterOS errors verbatim in the modal
        if current is not None:
            current.status = "failed"
            current.detail = str(exc)
        job.error = str(exc)
        job.status = "failed"
    finally:
        if transport is not None:
            transport.close()
