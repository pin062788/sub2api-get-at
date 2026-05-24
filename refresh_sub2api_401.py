#!/usr/bin/env python3
"""Refresh 401 ChatGPT accounts in sub2api by re-login and updating tokens."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import Parser
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse

try:
    from curl_cffi import requests as rq
except ImportError as exc:
    raise SystemExit("缺少依赖：请先运行 python3 -m pip install -r requirements.txt") from exc

ISSUER = "https://auth.openai.com"
CHATGPT_SESSION_URL = "https://chatgpt.com/api/auth/session"
CHATGPT_LOGIN_URL = "https://chatgpt.com/auth/login"
CHATGPT_AUTH_CSRF_URL = "https://chatgpt.com/api/auth/csrf"
CHATGPT_AUTH_SIGNIN_URL = "https://chatgpt.com/api/auth/signin/openai"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.150 Safari/537.36"
SCH = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
DEFAULT_MODEL_MAPPING = {model: model for model in [
    "gpt-4o-audio-preview", "gpt-4o-realtime-preview", "gpt-5.2", "gpt-5.2-2025-12-11",
    "gpt-5.2-chat-latest", "gpt-5.2-pro", "gpt-5.2-pro-2025-12-11", "gpt-5.3-codex",
    "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-2026-03-05", "gpt-5.4-mini", "gpt-5.5",
    "gpt-image-1", "gpt-image-1.5", "gpt-image-2",
]}
DEFAULT_EXPORT_META = {
    "extra": {},
    "concurrency": 10,
    "priority": 1,
    "rate_multiplier": 10,
    "auto_pause_on_expired": True,
}
PLAN_DISPLAY_NAMES = {
    "free": "free",
    "plus": "plus",
    "business": "Business",
    "pro_5x": "pro 5X",
    "pro_20x": "pro 20X",
}
LOCAL_CALLBACK_RE = re.compile(r"(https?://localhost[^\s'\"]+)")
OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
OTP_CONTEXT_RE = re.compile(r"(?:code|verification code|验证码)\D{0,40}(\d{6})(?!\d)", re.IGNORECASE)


def log(message: str, level: str = "INFO") -> None:
    print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] {level}: {message}", flush=True)


def merge(*items: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        result.update(item or {})
    return result


def rand_hex(byte_count: int) -> str:
    return secrets.token_hex(byte_count)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plus8_after(seconds: int) -> str:
    return (datetime.now(timezone(timedelta(hours=8))) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def safe_json_loads(raw: bytes | str | None) -> dict[str, Any]:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def resolve_plan_type(auth_claims: dict[str, Any], fallback: str = "free") -> str:
    joined = " ".join(normalize_text(item) for item in [
        auth_claims.get("chatgpt_plan_type"),
        auth_claims.get("plan_type"),
        auth_claims.get("subscription_plan"),
        auth_claims.get("account_plan"),
        fallback,
    ] if str(item or "").strip())
    if any(flag in joined for flag in ("pro20x", "pro20", "20x")):
        return "pro_20x"
    if any(flag in joined for flag in ("pro5x", "pro5", "5x")):
        return "pro_5x"
    if "business" in joined or "team" in joined:
        return "business"
    if "plus" in joined or "paid" in joined:
        return "plus"
    return "free"


def trace_headers() -> dict[str, str]:
    parent = secrets.randbelow(10**18)
    trace = secrets.randbelow(10**18)
    return {
        "traceparent": f"00-{rand_hex(16)}-{parent:016x}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace),
        "x-datadog-parent-id": str(parent),
    }


def pkce() -> tuple[str, str]:
    import hashlib

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def fnv1a32(text: str) -> str:
    value = 2166136261
    for char in text.encode():
        value = ((value ^ char) * 16777619) & 0xFFFFFFFF
    for mul, shift in ((2246822507, 16), (3266489909, 13)):
        value = ((value ^ (value >> shift)) * mul) & 0xFFFFFFFF
    return f"{(value ^ (value >> 16)) & 0xFFFFFFFF:08x}"


def extract_code_from_url(url: str) -> str:
    return dict(parse_qsl(urlparse(url).query)).get("code", "") if url else ""


class Sentinel:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.session_id = rand_hex(16)

    def config(self) -> list[Any]:
        now = time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime())
        lag = 1000 + secrets.randbelow(49000)
        return [
            "1920x1080", now, 4294705152, 1, UA, "https://sentinel.openai.com/sentinel/sdk.js",
            None, None, "en-US", "en-US,en", 0.5, "vendor-undefined", "location", "Object", lag,
            self.session_id, "", 8, int(time.time() * 1000) - lag,
        ]

    def request_payload(self) -> str:
        return "gAAAAAC" + base64.b64encode(json.dumps(self.config()).encode()).decode()

    def proof(self, seed: str, difficulty: str) -> str:
        config = self.config()
        start = time.time()
        for index in range(500000):
            config[3] = index
            config[9] = int((time.time() - start) * 1000)
            digest = base64.b64encode(json.dumps(config).encode()).decode()
            if fnv1a32(seed + digest)[: len(difficulty)] <= difficulty:
                return "gAAAAAB" + digest + "~S"
        return self.request_payload()


def build_sentinel(session: "AuthSession", device_id: str, flow: str) -> str:
    helper = Sentinel(device_id)
    response = session.request(
        "POST",
        "https://sentinel.openai.com/backend-api/sentinel/req",
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
            "Origin": "https://sentinel.openai.com",
            "User-Agent": UA,
            "sec-ch-ua": SCH,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        json={"p": helper.request_payload(), "id": device_id, "flow": flow},
        timeout=30,
    )
    data = response.json() if response.status_code == 200 else {}
    token = data.get("token", "")
    pow_data = data.get("proofofwork") or {}
    if not token:
        return ""
    proof = helper.proof(pow_data.get("seed", ""), str(pow_data.get("difficulty", ""))) if pow_data.get("required") else helper.request_payload()
    return json.dumps({"p": proof, "t": "", "c": token, "id": device_id, "flow": flow})


class AuthSession:
    def __init__(self, proxy: str = ""):
        self.client = rq.Session(impersonate="chrome131")
        if proxy:
            self.client.proxies = {"http": proxy, "https": proxy}

    def request(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("verify", False)
        return self.client.request(*args, **kwargs)

    def set_cookie(self, name: str, value: str, domain: str) -> None:
        self.client.cookies.set(name, value, domain=domain, path="/")

    def cookie(self, name: str) -> str:
        for cookie in list(getattr(self.client.cookies, "jar", [])):
            if getattr(cookie, "name", "") == name:
                return getattr(cookie, "value", "")
        return ""

    def do(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: str | bytes | None = None,
        follow: bool = False,
    ) -> dict[str, Any]:
        history: list[str] = []
        current = url
        req_headers = headers or {}
        for _ in range(20):
            try:
                response = self.client.request(
                    method,
                    current,
                    headers=req_headers,
                    json=json_body,
                    data=data,
                    timeout=60,
                    allow_redirects=False,
                    verify=False,
                )
            except Exception as error:
                match = LOCAL_CALLBACK_RE.search(str(error))
                return {"status": 0, "final": match.group(1) if match else current, "body": b"", "loc": "", "hist": history}
            result = {
                "status": response.status_code,
                "final": current,
                "body": response.content,
                "loc": response.headers.get("Location", ""),
                "hist": history,
            }
            if not follow or response.status_code not in (301, 302, 303, 307, 308) or not result["loc"]:
                return result
            history.append(current)
            parsed = urlparse(current)
            current = result["loc"] if result["loc"].startswith("http") else f"{parsed.scheme}://{parsed.netloc}{result['loc']}"
            method = "GET"
            json_body = None
            data = None
            req_headers = merge(req_headers, {"Referer": current})
        return {"status": 0, "final": current, "body": b"", "loc": "", "hist": history}


def follow_for_code(session: AuthSession, start: str, referer: str = "") -> tuple[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": UA,
        **({"Referer": referer} if referer else {}),
    }
    current = start
    for _ in range(16):
        response = session.do("GET", current, headers)
        code = extract_code_from_url(response["final"]) or extract_code_from_url(response["loc"])
        if code or not response["loc"] or response["status"] not in (301, 302, 303, 307, 308):
            return code, response["final"]
        headers["Referer"] = response["final"]
        current = response["loc"] if response["loc"].startswith("http") else f"{ISSUER}{response['loc']}"
    return "", current


def allow_redirect_extract_code(session: AuthSession, url: str, referer: str = "") -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": UA,
        **({"Referer": referer} if referer else {}),
    }
    response = session.do("GET", url, headers, follow=True)
    code = extract_code_from_url(response["final"])
    if code:
        return code
    for history_url in response["hist"]:
        code = extract_code_from_url(history_url)
        if code:
            return code
    return ""


def decode_session_cookie(session: AuthSession) -> dict[str, Any]:
    raw = session.cookie("oai-client-auth-session")
    if not raw:
        return {}
    raw = quote(raw, safe=".%").replace("%25", "%") if "%" in raw else raw
    try:
        payload = raw.split(".", 1)[0]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_auth_context(data: Any) -> dict[str, str]:
    context: dict[str, str] = {}
    stack = [data] if isinstance(data, (dict, list)) else []
    auth_step_keys = {"authorization_step", "authorizationstep", "auth_step", "authstep", "current_authorization_step", "currentauthorizationstep"}
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                normalized = str(key).lower()
                if normalized in auth_step_keys and isinstance(value, (str, int)):
                    context["authorization_step"] = str(value)
                elif normalized in ("email", "email_address") and isinstance(value, str) and "@" in value:
                    context.setdefault("email", value)
                elif normalized in ("masked_email", "redacted_email") and isinstance(value, str):
                    context.setdefault(normalized, value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list)))
    return context


def email_otp_payload(auth_context: dict[str, str], code: str = "") -> dict[str, str]:
    payload = {key: auth_context[key] for key in ("authorization_step",) if auth_context.get(key)}
    if code:
        payload["code"] = code
    return payload


def submit_workspace_org(session: AuthSession, device_id: str, consent_url: str) -> str:
    cookie = decode_session_cookie(session)
    workspace = ((cookie.get("workspaces") or [{}])[0] or {}).get("id", "")
    if not workspace:
        return ""
    headers = merge({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ISSUER,
        "Referer": consent_url,
        "User-Agent": UA,
        "oai-device-id": device_id,
    }, trace_headers())
    response = session.do("POST", f"{ISSUER}/api/accounts/workspace/select", headers, json_body={"workspace_id": workspace})
    body = safe_json_loads(response["body"])
    loc = response["loc"] if response["loc"].startswith("http") else f"{ISSUER}{response['loc']}" if response["loc"] else body.get("continue_url", "")
    if extract_code_from_url(loc):
        return extract_code_from_url(loc)
    if response["status"] in (301, 302, 303, 307, 308) and loc:
        code, final_url = follow_for_code(session, loc, consent_url)
        return code or allow_redirect_extract_code(session, final_url or loc, consent_url)
    orgs = (body.get("data") or {}).get("orgs") or []
    if orgs:
        payload = {"org_id": orgs[0].get("id")}
        projects = orgs[0].get("projects") or []
        if projects and projects[0].get("id"):
            payload["project_id"] = projects[0]["id"]
        org_referer = body.get("continue_url", "") or loc or consent_url
        if org_referer.startswith("/"):
            org_referer = f"{ISSUER}{org_referer}"
        response = session.do("POST", f"{ISSUER}/api/accounts/organization/select", merge(headers, {"Referer": org_referer}), json_body=payload)
        org_body = safe_json_loads(response["body"])
        loc = response["loc"] if response["loc"].startswith("http") else f"{ISSUER}{response['loc']}" if response["loc"] else ""
        if extract_code_from_url(loc):
            return extract_code_from_url(loc)
        if response["status"] in (301, 302, 303, 307, 308) and loc:
            code, final_url = follow_for_code(session, loc, org_referer)
            return code or allow_redirect_extract_code(session, final_url or loc, org_referer)
        next_url = org_body.get("continue_url", "")
        if next_url:
            next_url = f"{ISSUER}{next_url}" if next_url.startswith("/") else next_url
            code, final_url = follow_for_code(session, next_url, org_referer)
            return code or allow_redirect_extract_code(session, final_url or next_url, org_referer)
    next_url = body.get("continue_url", "")
    if next_url:
        next_url = f"{ISSUER}{next_url}" if next_url.startswith("/") else next_url
        code, final_url = follow_for_code(session, next_url, consent_url)
        return code or allow_redirect_extract_code(session, final_url or next_url, consent_url)
    return ""


def fetch_chatgpt_session(session: AuthSession) -> dict[str, Any]:
    try:
        response = session.client.get(
            CHATGPT_SESSION_URL,
            headers={"Accept": "application/json", "User-Agent": UA, "Referer": "https://chatgpt.com/"},
            timeout=30,
            verify=False,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return {}
    return {}


@dataclass
class MailMessage:
    id: str
    address: str
    original_recipient: str
    subject: str
    body: str
    raw: str
    received_at: str


class TempEmailClient:
    def __init__(self, base_url: str, admin_auth: str, custom_auth: str = "", insecure: bool = True, mail_paths: list[str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.admin_auth = admin_auth
        self.custom_auth = custom_auth
        self.insecure = insecure
        self.mail_paths = mail_paths or [
            "/admin/mails", "/admin/messages", "/admin/mail", "/api/mails", "/api/messages", "/mails", "/messages",
        ]
        self.session = rq.Session(impersonate="chrome131")

    def headers(self, json_body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": UA}
        if self.admin_auth:
            headers["x-admin-auth"] = self.admin_auth
        if self.custom_auth:
            headers["x-custom-auth"] = self.custom_auth
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            params={key: value for key, value in (params or {}).items() if value not in (None, "")},
            json=payload,
            headers=self.headers(json_body=payload is not None),
            timeout=25,
            verify=not self.insecure,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Temp Email 请求失败：HTTP {response.status_code} {response.text[:160]}")
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f"Temp Email 返回非 JSON：{response.text[:160]}") from exc

    def ensure_address(self, email: str, domain: str) -> str:
        local_part, _, email_domain = email.partition("@")
        if email_domain.lower() != domain.lower():
            return email
        payload = {"enablePrefix": True, "enableRandomSubdomain": False, "name": local_part, "domain": domain}
        try:
            data = self.request_json("POST", "/admin/new_address", payload)
            address = first_non_empty(data, ["address", "email", "data.address", "data.email"])
            return address.lower() if address else email
        except Exception as error:
            log(f"创建/确认临时邮箱失败，继续尝试直接收信：{error}", "WARN")
            return email

    def list_messages(self, email: str, since_ts: float) -> list[MailMessage]:
        errors: list[str] = []
        params = {
            "address": email,
            "email": email,
            "recipient": email,
            "page": 1,
            "pageSize": 50,
            "page_size": 50,
            "offset": 0,
            "limit": 50,
        }
        for path in self.mail_paths:
            try:
                data = self.request_json("GET", path, params=params)
                messages = normalize_mail_messages(data)
                filtered = [item for item in messages if message_matches(item, email, since_ts)]
                if filtered or messages:
                    return filtered
            except Exception as error:
                errors.append(f"{path}: {str(error)[:100]}")
        raise RuntimeError("Temp Email 收信端点不可用：" + " | ".join(errors[:4]))

    def wait_otp(self, email: str, since_ts: float, excluded: set[str], timeout: int = 180, interval: int = 5) -> str:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            try:
                messages = self.list_messages(email, since_ts)
                for message in sorted(messages, key=lambda item: item.received_at, reverse=True):
                    code = extract_otp(f"{message.subject}\n{message.body}\n{message.raw}", excluded)
                    if code:
                        return code
            except Exception as error:
                last_error = str(error)
            time.sleep(interval)
        raise TimeoutError(f"等待验证码超时：{email}；最后错误：{last_error}")


def first_non_empty(data: Any, paths: list[str]) -> str:
    for path in paths:
        current = data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = None
                break
        value = str(current or "").strip()
        if value:
            return value
    return ""


def rows_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "messages", "mails", "results", "rows", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows_from_payload(value)
            if nested:
                return nested
    return []


def html_to_text(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", value or "")).replace("\xa0", " ")


def parse_raw_mail(raw: str) -> tuple[str, str, str]:
    if not raw:
        return "", "", ""
    try:
        parsed = Parser(policy=policy.default).parsestr(raw)
        subject = str(parsed.get("subject") or "")
        sender = str(parsed.get("from") or "")
        if parsed.is_multipart():
            parts = []
            for part in parsed.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                try:
                    parts.append(part.get_content())
                except Exception:
                    pass
            body = " ".join(str(part) for part in parts)
        else:
            body = str(parsed.get_content())
        return subject, sender, html_to_text(body)
    except Exception:
        return "", "", html_to_text(raw)


def normalize_mail_messages(payload: Any) -> list[MailMessage]:
    messages: list[MailMessage] = []
    for row in rows_from_payload(payload):
        if not isinstance(row, dict):
            continue
        raw = str(row.get("raw") or row.get("source") or row.get("mime") or row.get("message") or "")
        raw_subject, _raw_sender, raw_body = parse_raw_mail(raw)
        body = " ".join(str(row.get(key) or "") for key in ("text", "preview", "body", "bodyPreview", "html"))
        messages.append(MailMessage(
            id=str(row.get("id") or row.get("mail_id") or ""),
            address=str(row.get("address") or row.get("mail_address") or row.get("email") or row.get("recipient") or "").strip().lower(),
            original_recipient=str(row.get("original_recipient") or row.get("originalRecipient") or row.get("to") or "").strip().lower(),
            subject=str(row.get("subject") or raw_subject or ""),
            body=html_to_text(body or raw_body),
            raw=raw,
            received_at=str(row.get("receivedDateTime") or row.get("received_at") or row.get("created_at") or row.get("createdAt") or row.get("date") or ""),
        ))
    return messages


def parse_message_time(value: str) -> float:
    value = str(value or "").strip()
    if not value:
        return 0
    if value.isdigit():
        numeric = int(value)
        return numeric / 1000 if numeric > 10**11 else float(numeric)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0


def message_matches(message: MailMessage, email: str, since_ts: float) -> bool:
    target = email.strip().lower()
    recipients = {message.address, message.original_recipient}
    if target and target not in recipients and all(target not in text for text in [message.body.lower(), message.raw.lower()]):
        return False
    received_ts = parse_message_time(message.received_at)
    return not received_ts or received_ts >= since_ts - 60


def extract_otp(text: str, excluded: set[str]) -> str:
    for pattern in (OTP_CONTEXT_RE, OTP_RE):
        for code in pattern.findall(text or ""):
            if code not in excluded:
                return code
    return ""


class Sub2ApiClient:
    def __init__(self, base_url: str, email: str, password: str, proxy: str = ""):
        self.base_url = base_url.rstrip("/")
        self.session = rq.Session(impersonate="chrome131")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        login_data = self.req("POST", "/api/v1/auth/login", {"email": email, "password": password}, token="")
        self.token = login_data.get("access_token", "")
        if not self.token:
            raise RuntimeError("sub2api 登录失败")

    def req(self, method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> Any:
        auth = self.token if token is None else token
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **({"Authorization": f"Bearer {auth}"} if auth else {}),
            },
            timeout=30,
            verify=False,
        )
        raw = response.text or ""
        try:
            parsed = response.json()
        except Exception:
            parsed = {}
        if response.status_code >= 400:
            raise RuntimeError(str(parsed.get("message") or raw or f"HTTP {response.status_code}")[:240])
        if isinstance(parsed, dict) and "code" in parsed and parsed.get("code") not in (0, 200, None):
            raise RuntimeError(str(parsed.get("message") or raw or "sub2api 请求失败")[:240])
        return parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed

    def list_accounts(self, page_size: int = 200) -> list[dict[str, Any]]:
        page = 1
        result: list[dict[str, Any]] = []
        while True:
            data = self.req("GET", f"/api/v1/admin/accounts?page={page}&page_size={page_size}&platform=openai&type=oauth") or {}
            items = data.get("items") or data.get("list") or []
            result.extend(items)
            total = int(data.get("total") or len(result))
            if not items or len(result) >= total:
                break
            page += 1
        return result

    def group_ids_by_name(self, group_name: str) -> list[int]:
        groups = self.req("GET", "/api/v1/admin/groups/all") or []
        target = group_name.strip().lower()
        ids = [int(item.get("id")) for item in groups if str(item.get("name") or "").strip().lower() == target and str(item.get("platform") or "").lower() == "openai"]
        if not ids:
            raise RuntimeError(f"sub2api 未找到 openai 分组：{group_name}")
        return ids

    def put_account(self, account: dict[str, Any], credentials: dict[str, Any], group_ids: list[int]) -> dict[str, Any]:
        account_id = int(account.get("id") or 0)
        if account_id <= 0:
            raise RuntimeError("账号缺少 id，无法更新")
        payload = {
            "name": account.get("name") or credentials.get("email") or f"account-{account_id}",
            "notes": account.get("notes") or "",
            "platform": "openai",
            "type": "oauth",
            "credentials": credentials,
            "group_ids": account.get("group_ids") or group_ids,
            "concurrency": account.get("concurrency") or DEFAULT_EXPORT_META["concurrency"],
            "priority": account.get("priority") or DEFAULT_EXPORT_META["priority"],
            "rate_multiplier": account.get("rate_multiplier") or DEFAULT_EXPORT_META["rate_multiplier"],
            "auto_pause_on_expired": account.get("auto_pause_on_expired") if account.get("auto_pause_on_expired") is not None else True,
        }
        if account.get("proxy_id"):
            payload["proxy_id"] = account.get("proxy_id")
        return self.req("PUT", f"/api/v1/admin/accounts/{account_id}", payload)

    def create_account(self, account: dict[str, Any], credentials: dict[str, Any], group_ids: list[int]) -> dict[str, Any]:
        payload = merge(DEFAULT_EXPORT_META, {
            "name": account.get("name") or credentials.get("email") or "ChatGPT Account",
            "notes": account.get("notes") or "",
            "platform": "openai",
            "type": "oauth",
            "credentials": credentials,
            "group_ids": group_ids,
        })
        if account.get("proxy_id"):
            payload["proxy_id"] = account.get("proxy_id")
        return self.req("POST", "/api/v1/admin/accounts", payload)


def account_email(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") or {}
    return str(credentials.get("email") or account.get("email") or account.get("name") or "").strip().lower()


def is_401_account(account: dict[str, Any]) -> bool:
    values = [
        account.get("status"),
        account.get("error_message"),
        account.get("temp_unschedulable_reason"),
        account.get("session_window_status"),
        account.get("credentials_status"),
    ]
    text = " ".join(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "") for value in values)
    return "401" in text or "unauthorized" in text.lower()


def build_credentials(token_json: dict[str, Any], session_json: dict[str, Any], email: str, fallback_plan: str) -> dict[str, Any]:
    access_token = str(session_json.get("accessToken") or session_json.get("access_token") or token_json.get("access_token") or "")
    if not access_token:
        raise RuntimeError("缺少 access token")
    access_payload = decode_jwt_payload(access_token)
    id_payload = decode_jwt_payload(str(token_json.get("id_token") or ""))
    auth_claims = access_payload.get("https://api.openai.com/auth") or id_payload.get("https://api.openai.com/auth") or {}
    profile = access_payload.get("https://api.openai.com/profile") or {}
    user = session_json.get("user") if isinstance(session_json.get("user"), dict) else {}
    plan_key = resolve_plan_type(auth_claims, fallback_plan)
    credentials: dict[str, Any] = {
        "model_mapping": dict(DEFAULT_MODEL_MAPPING),
        "access_token": access_token,
        "client_id": str(token_json.get("client_id") or CLIENT_ID),
        "email": email or user.get("email") or profile.get("email") or id_payload.get("email") or auth_claims.get("email") or "",
        "expires_at": plus8_after(int(token_json.get("expires_in") or 3600)),
        "plan_type": PLAN_DISPLAY_NAMES[plan_key],
    }
    for key in ("refresh_token", "id_token"):
        value = token_json.get(key)
        if value:
            credentials[key] = value
    for target, candidates in {
        "chatgpt_account_id": ["chatgpt_account_id"],
        "chatgpt_user_id": ["chatgpt_user_id", "user_id"],
        "organization_id": ["organization_id"],
    }.items():
        for candidate in candidates:
            if auth_claims.get(candidate):
                credentials[target] = auth_claims[candidate]
                break
    return credentials


def chatgpt_nav_headers(referer: str = "https://chatgpt.com/") -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": UA,
        "sec-ch-ua": SCH,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


def chatgpt_api_headers(referer: str = CHATGPT_LOGIN_URL, form: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://chatgpt.com",
        "Referer": referer,
        "User-Agent": UA,
        "sec-ch-ua": SCH,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return headers


def chatgpt_web_login(email: str, otp_callback: Any, proxy: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    session = AuthSession(proxy)
    login_page = session.do("GET", CHATGPT_LOGIN_URL, chatgpt_nav_headers(), follow=False)
    if login_page["status"] >= 400:
        raise RuntimeError(f"ChatGPT 登录页不可用：HTTP {login_page['status']}")
    csrf_resp = session.do("GET", CHATGPT_AUTH_CSRF_URL, chatgpt_api_headers(), follow=False)
    csrf = safe_json_loads(csrf_resp["body"]).get("csrfToken", "")
    if csrf_resp["status"] != 200 or not csrf:
        raise RuntimeError(f"ChatGPT CSRF 获取失败：HTTP {csrf_resp['status']}")
    signin_resp = session.do("POST", CHATGPT_AUTH_SIGNIN_URL, chatgpt_api_headers(form=True), data=urlencode({
        "csrfToken": csrf,
        "callbackUrl": "https://chatgpt.com/",
        "json": "true",
    }))
    signin_json = safe_json_loads(signin_resp["body"])
    auth_url = str(signin_json.get("url") or signin_resp["loc"] or "")
    if auth_url.startswith("/"):
        auth_url = f"https://chatgpt.com{auth_url}"
    if signin_resp["status"] >= 400 or not auth_url:
        raise RuntimeError(f"ChatGPT 登录入口获取失败：HTTP {signin_resp['status']} {str(signin_json)[:200]}")
    response = session.do("GET", auth_url, chatgpt_nav_headers(CHATGPT_LOGIN_URL), follow=True)
    referer = response["final"] if str(response["final"]).startswith(ISSUER) else f"{ISSUER}/log-in"
    device_id = ""
    for cookie in list(getattr(session.client.cookies, "jar", [])):
        if getattr(cookie, "name", "") == "oai-did":
            device_id = getattr(cookie, "value", "")
            break
    if not device_id:
        device_id = rand_hex(16)
        session.set_cookie("oai-did", device_id, "auth.openai.com")
        session.set_cookie("oai-did", device_id, ".auth.openai.com")
    continue_headers = merge({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ISSUER,
        "Referer": referer,
        "User-Agent": UA,
        "oai-device-id": device_id,
        "openai-sentinel-token": build_sentinel(session, device_id, "authorize_continue"),
    }, trace_headers())
    continue_resp = session.do("POST", f"{ISSUER}/api/accounts/authorize/continue", continue_headers, json_body={"username": {"kind": "email", "value": email}})
    continue_json = safe_json_loads(continue_resp["body"])
    auth_context = extract_auth_context(continue_json)
    if continue_resp["status"] >= 400:
        raise RuntimeError(f"提交登录邮箱失败：HTTP {continue_resp['status']} {str(continue_json)[:240]}")
    continue_url = str(continue_json.get("continue_url") or "")
    if continue_url:
        otp_page_url = f"{ISSUER}{continue_url}" if continue_url.startswith("/") else continue_url
        session.do("GET", otp_page_url, merge(chatgpt_nav_headers(referer), {"oai-device-id": device_id}))
    auth_context = merge(auth_context, extract_auth_context(decode_session_cookie(session)))
    excluded: set[str] = set()
    otp_headers = merge({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ISSUER,
        "Referer": f"{ISSUER}/email-verification",
        "User-Agent": UA,
        "oai-device-id": device_id,
    }, trace_headers())
    for _ in range(4):
        otp = otp_callback(email, excluded)
        otp_resp = session.do("POST", f"{ISSUER}/api/accounts/email-otp/validate", otp_headers, json_body=email_otp_payload(auth_context, otp))
        otp_json = safe_json_loads(otp_resp["body"])
        auth_context = merge(auth_context, extract_auth_context(otp_json))
        if otp_resp["status"] == 200:
            next_page_type = (otp_json.get("page") or {}).get("type") or ""
            if next_page_type == "add_phone":
                raise RuntimeError("OpenAI 要求补手机号验证，当前普通网页登录流程无法继续")
            continue_url = str(otp_json.get("continue_url") or continue_url)
            break
        excluded.add(otp)
    else:
        raise RuntimeError("验证码校验失败")
    if continue_url:
        continue_url = f"{ISSUER}{continue_url}" if continue_url.startswith("/") else continue_url
        session.do("GET", continue_url, chatgpt_nav_headers(f"{ISSUER}/email-verification"), follow=True)
    session_json = fetch_chatgpt_session(session)
    if not (session_json.get("accessToken") or session_json.get("access_token")):
        raise RuntimeError("普通网页登录成功后未获取 ChatGPT session access token")
    return {"client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH", "expires_in": 3600}, session_json


def oauth_authorize(email: str, otp_callback: Any, proxy: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    session = AuthSession(proxy)
    device_id = rand_hex(16)
    session.set_cookie("oai-did", device_id, "auth.openai.com")
    session.set_cookie("oai-did", device_id, ".auth.openai.com")
    verifier, challenge = pkce()
    auth_url = f"{ISSUER}/oauth/authorize?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": secrets.token_urlsafe(24),
    })
    response = session.do("GET", auth_url, {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://chatgpt.com/",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": UA,
    }, follow=True)
    referer = response["final"] if str(response["final"]).startswith(ISSUER) else f"{ISSUER}/log-in"
    continue_headers = merge({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ISSUER,
        "Referer": referer,
        "User-Agent": UA,
        "oai-device-id": device_id,
        "openai-sentinel-token": build_sentinel(session, device_id, "authorize_continue"),
    }, trace_headers())
    continue_resp = session.do("POST", f"{ISSUER}/api/accounts/authorize/continue", continue_headers, json_body={"username": {"kind": "email", "value": email}})
    continue_json = safe_json_loads(continue_resp["body"])
    auth_context = extract_auth_context(continue_json)
    if continue_resp["status"] >= 400:
        raise RuntimeError(f"提交登录邮箱失败：HTTP {continue_resp['status']} {str(continue_json)[:240]}")
    continue_url = continue_json.get("continue_url", "")
    page_type = (continue_json.get("page") or {}).get("type") or ""
    if continue_url:
        otp_page_url = f"{ISSUER}{continue_url}" if str(continue_url).startswith("/") else continue_url
        session.do("GET", otp_page_url, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": UA,
            "oai-device-id": device_id,
        })
    auth_context = merge(auth_context, extract_auth_context(decode_session_cookie(session)))
    excluded: set[str] = set()
    otp_headers = merge({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ISSUER,
        "Referer": f"{ISSUER}/email-verification",
        "User-Agent": UA,
        "oai-device-id": device_id,
    }, trace_headers())
    for attempt in range(4):
        if attempt > 0 or page_type != "email_otp_verification":
            resend_resp = session.do("POST", f"{ISSUER}/api/accounts/email-otp/send", otp_headers, json_body=email_otp_payload(auth_context))
            resend_json = safe_json_loads(resend_resp["body"])
            auth_context = merge(auth_context, extract_auth_context(resend_json))
            if resend_resp["status"] >= 400:
                raise RuntimeError(f"验证码发送失败：HTTP {resend_resp['status']} {str(resend_json)[:200]}")
        otp = otp_callback(email, excluded)
        otp_resp = session.do("POST", f"{ISSUER}/api/accounts/email-otp/validate", otp_headers, json_body=email_otp_payload(auth_context, otp))
        otp_json = safe_json_loads(otp_resp["body"])
        auth_context = merge(auth_context, extract_auth_context(otp_json))
        if otp_resp["status"] == 200:
            next_page_type = (otp_json.get("page") or {}).get("type") or ""
            if next_page_type == "add_phone":
                raise RuntimeError("OpenAI 要求补手机号验证，当前邮箱验证码流程无法继续获取 authorization code")
            continue_url = otp_json.get("continue_url", continue_url)
            break
        excluded.add(otp)
    else:
        raise RuntimeError("验证码校验失败")
    continue_url = f"{ISSUER}{continue_url}" if str(continue_url).startswith("/") else continue_url
    direct_code = extract_code_from_url(continue_url)
    follow_code = ""
    followed_url = ""
    if not direct_code and continue_url:
        follow_code, followed_url = follow_for_code(session, continue_url, f"{ISSUER}/email-verification")
    workspace_code = ""
    consent_url = followed_url or continue_url or f"{ISSUER}/sign-in-with-chatgpt/codex/consent"
    if not direct_code and not follow_code:
        workspace_code = submit_workspace_org(session, device_id, consent_url)
    fallback_code = ""
    if not direct_code and not follow_code and not workspace_code:
        for candidate in [followed_url, continue_url, f"{ISSUER}/sign-in-with-chatgpt/codex/consent"]:
            candidate = str(candidate or "").strip()
            if not candidate:
                continue
            candidate_code, final_url = follow_for_code(session, candidate, f"{ISSUER}/email-verification")
            fallback_code = candidate_code or allow_redirect_extract_code(session, final_url or candidate, f"{ISSUER}/email-verification")
            if fallback_code:
                break
    code = direct_code or follow_code or workspace_code or fallback_code
    if not code:
        raise RuntimeError("未获取 authorization code")
    token_resp = session.do("POST", f"{ISSUER}/oauth/token", {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": UA,
    }, data=urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }))
    token_json = safe_json_loads(token_resp["body"])
    if token_resp["status"] != 200 or not token_json.get("access_token"):
        raise RuntimeError(f"OAuth token 交换失败：HTTP {token_resp['status']} {str(token_json)[:240]}")
    return token_json, fetch_chatgpt_session(session)


def save_queue(path: str, accounts: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for account in accounts:
            email = account_email(account)
            if email:
                handle.write(f"{email}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh sub2api 401 ChatGPT accounts")
    parser.add_argument("--sub-base-url", default="http://localhost:8080")
    parser.add_argument("--sub-admin-email", default="your real info")
    parser.add_argument("--sub-admin-password", default="your real info")
    parser.add_argument("--sub-group", default="your real info")
    parser.add_argument("--temp-api", default="your real info")
    parser.add_argument("--temp-admin-auth", default="your real info")
    parser.add_argument("--temp-custom-auth", default="")
    parser.add_argument("--temp-domain", default="your real info")
    parser.add_argument("--temp-mail-paths", default="", help="逗号分隔的收信端点；不传则尝试常见端点")
    parser.add_argument("--queue-file", default="401_accounts.txt")
    parser.add_argument("--save-queue", action="store_true", help="只额外保存 401 邮箱队列，不影响默认流式处理")
    parser.add_argument("--dry-run", action="store_true", help="只扫描 401 邮箱，不登录、不更新")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--otp-timeout", type=int, default=180)
    parser.add_argument("--otp-interval", type=int, default=5)
    parser.add_argument("--create-on-update-fail", action="store_true", help="PUT 更新失败时改为创建新账号")
    args = parser.parse_args()

    sub = Sub2ApiClient(args.sub_base_url, args.sub_admin_email, args.sub_admin_password, args.proxy)
    group_ids = sub.group_ids_by_name(args.sub_group)
    accounts = [account for account in sub.list_accounts() if is_401_account(account) and account_email(account)]
    if args.limit > 0:
        accounts = accounts[: args.limit]
    log(f"扫描到 401 账号 {len(accounts)} 个")
    if args.save_queue or args.dry_run:
        save_queue(args.queue_file, accounts)
        log(f"401 邮箱队列已保存：{os.path.abspath(args.queue_file)}")
    if args.dry_run or not accounts:
        return 0

    mail_paths = [item.strip() for item in args.temp_mail_paths.split(",") if item.strip()]
    temp_mail = TempEmailClient(args.temp_api, args.temp_admin_auth, args.temp_custom_auth, mail_paths=mail_paths or None)

    success = 0
    failed = 0
    for account in accounts:
        email = account_email(account)
        log(f"开始补号：{email}")
        started_at = time.time()
        temp_mail.ensure_address(email, args.temp_domain)

        def otp_callback(current_email: str, excluded: set[str]) -> str:
            log(f"等待邮箱验证码：{current_email}")
            code = temp_mail.wait_otp(current_email, started_at, excluded, timeout=args.otp_timeout, interval=args.otp_interval)
            log(f"已获取邮箱验证码：{current_email}")
            return code

        try:
            token_json, session_json = chatgpt_web_login(email, otp_callback, args.proxy)
            credentials = build_credentials(token_json, session_json, email, str((account.get("credentials") or {}).get("plan_type") or "free"))
            try:
                updated = sub.put_account(account, credentials, group_ids)
            except Exception:
                if not args.create_on_update_fail:
                    raise
                updated = sub.create_account(account, credentials, group_ids)
            success += 1
            log(f"补号完成：{email} -> sub2api #{updated.get('id') or account.get('id')}", "OK")
        except Exception as error:
            failed += 1
            log(f"补号失败：{email}；{error}", "ERROR")
    log(f"完成：成功 {success}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())