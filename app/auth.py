"""Вход через корпоративный OpenID Connect (код авторизации + PKCE) и права доступа к курсам.

Своих логинов и паролей в приложении нет. Режим AUTH_MODE=off отключает вход и нужен только для разработки.
Права: владелец курса, коллеги с правом «просмотр» или «редактирование» (Course.shares), администраторы из ADMIN_EMAILS.
"""
from __future__ import annotations
import base64
import hashlib
import logging
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

import jwt
import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config, storage

log = logging.getLogger("auth")
router = APIRouter()

LOCAL_USER = {"email": "local@dev", "name": "Локальный режим (без входа)", "sub": "local"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COURSE_PATH = re.compile(r"^/api/courses/([0-9a-f]{12})(/.*)?$")

_cache_lock = threading.Lock()
_cache: dict = {"conf": None, "conf_at": 0.0, "jwks": None, "jwks_at": 0.0}


class AuthError(Exception):
    pass


def enabled() -> bool:
    return config.AUTH_MODE != "off"


def _verify():
    return config.OIDC_CA_BUNDLE or True


def _get_json(url: str, **kw) -> dict:
    try:
        r = requests.get(url, timeout=20, verify=_verify(), **kw)
    except requests.exceptions.SSLError as e:
        raise AuthError("Ошибка сертификата провайдера входа. Укажите файл сертификатов в OIDC_CA_BUNDLE.") from e
    except requests.RequestException as e:
        raise AuthError(f"Провайдер входа недоступен: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise AuthError(f"Провайдер входа вернул ошибку {r.status_code}")
    return r.json()


def _conf() -> dict:
    with _cache_lock:
        if _cache["conf"] and time.time() - _cache["conf_at"] < 3600:
            return _cache["conf"]
    conf = _get_json(f"{config.OIDC_ISSUER}/.well-known/openid-configuration")
    with _cache_lock:
        _cache["conf"], _cache["conf_at"] = conf, time.time()
    return conf


def _signing_key(kid: str | None):
    for force in (False, True):  # второй проход: ключи у провайдера могли смениться
        with _cache_lock:
            fresh = _cache["jwks"] and time.time() - _cache["jwks_at"] < 600 and not force
            jwks = _cache["jwks"] if fresh else None
        if jwks is None:
            jwks = _get_json(_conf()["jwks_uri"])
            with _cache_lock:
                _cache["jwks"], _cache["jwks_at"] = jwks, time.time()
        for k in jwks.get("keys", []):
            if kid is None or k.get("kid") == kid:
                return jwt.PyJWK.from_dict(k).key
    raise AuthError("Не найден ключ для проверки подписи токена.")


def _redirect_uri(request: Request) -> str:
    return config.OIDC_REDIRECT_URI or str(request.url_for("auth_callback"))


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def current_user(request: Request) -> dict | None:
    if not enabled():
        return dict(LOCAL_USER)
    return request.session.get("user")


def is_admin(user: dict | None) -> bool:
    if not user:
        return False
    if not enabled():
        return True
    return (user.get("email") or "").lower() in config.ADMIN_EMAILS


def course_access(course, user: dict) -> str | None:
    """admin / owner / edit / view или None, если курса для пользователя не существует."""
    if is_admin(user):
        return "admin"
    email = (user.get("email") or "").lower()
    if course.owner and course.owner.lower() == email:
        return "owner"
    for sh in course.shares:
        if sh.email.lower() == email:
            return sh.role
    return None


_RANK = {"view": 1, "edit": 2, "owner": 3, "admin": 3}


def check_request(request: Request, user: dict) -> JSONResponse | None:
    """Проверка прав для адресов /api/courses/<id>/...: чтение - просмотр, изменение - редактирование,
    удаление курса и настройка доступа - владелец или администратор."""
    m = _COURSE_PATH.match(request.url.path)
    if not m:
        return None
    course_id, rest = m.group(1), m.group(2) or ""
    try:
        course = storage.load_course(course_id)
    except FileNotFoundError:
        return JSONResponse({"detail": "Курс не найден"}, status_code=404)
    access = course_access(course, user)
    if access is None:
        return JSONResponse({"detail": "Курс не найден"}, status_code=404)
    request.state.access = access
    need = "view" if request.method in ("GET", "HEAD", "OPTIONS") else "edit"
    if rest.startswith("/shares") or (rest == "" and request.method == "DELETE"):
        need = "owner"
    if _RANK[access] < _RANK[need]:
        text = {"edit": "У вас доступ только для просмотра этого курса.", "owner": "Это может сделать только владелец курса."}[need]
        return JSONResponse({"detail": text}, status_code=403)
    return None


def normalize_shares(items, owner: str | None) -> list[dict]:
    out, seen = [], {(owner or "").lower()}
    for it in items or []:
        email = str((it or {}).get("email", "")).strip().lower()
        role = (it or {}).get("role", "view")
        if role not in ("view", "edit"):
            role = "view"
        if not email or email in seen:
            continue
        if not EMAIL_RE.match(email):
            raise ValueError(f"Некорректный адрес почты: {email}")
        seen.add(email)
        out.append({"email": email, "role": role})
    return out


# ---------- маршруты входа ----------

def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _error_page(text: str, status: int = 400) -> HTMLResponse:
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход не выполнен</title><body style="font-family:Arial,sans-serif;max-width:520px;margin:80px auto;padding:0 16px;color:#1a1a1a">
<h2>Не удалось войти</h2><p>{text}</p><p><a href="/auth/login">Попробовать снова</a></p></body>"""
    return HTMLResponse(html, status_code=status)


@router.get("/auth/login")
def auth_login(request: Request, next: str = "/"):
    if not enabled():
        return RedirectResponse(_safe_next(next))
    try:
        conf = _conf()
    except AuthError as e:
        return _error_page(str(e), 502)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    request.session["oidc"] = {"state": state, "nonce": nonce, "verifier": verifier, "next": _safe_next(next)}
    params = {
        "response_type": "code", "client_id": config.OIDC_CLIENT_ID, "redirect_uri": _redirect_uri(request),
        "scope": config.OIDC_SCOPES, "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{conf['authorization_endpoint']}?{urlencode(params)}")


@router.get("/auth/callback", name="auth_callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return _error_page(f"Провайдер входа вернул ошибку: {error} {error_description}".strip())
    saved = request.session.pop("oidc", None)
    if not saved or not code or not secrets.compare_digest(saved["state"], state):
        return _error_page("Сессия входа устарела. Начните вход заново.")
    try:
        conf = _conf()
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(request),
                "code_verifier": saved["verifier"]}
        auth = None
        if config.OIDC_TOKEN_AUTH == "basic":
            auth = (config.OIDC_CLIENT_ID, config.OIDC_CLIENT_SECRET)
        else:
            data["client_id"] = config.OIDC_CLIENT_ID
            data["client_secret"] = config.OIDC_CLIENT_SECRET
        try:
            r = requests.post(conf["token_endpoint"], data=data, auth=auth, timeout=20, verify=_verify())
        except requests.RequestException as e:
            raise AuthError(f"Провайдер входа недоступен: {e.__class__.__name__}") from e
        if r.status_code != 200:
            log.warning("token endpoint: %s %s", r.status_code, r.text[:300])
            raise AuthError("Провайдер не принял вход. Проверьте адрес возврата и секрет клиента.")
        tokens = r.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise AuthError("Провайдер не вернул id_token.")
        header = jwt.get_unverified_header(id_token)
        claims = jwt.decode(
            id_token, _signing_key(header.get("kid")), algorithms=["RS256", "RS384", "RS512", "ES256", "PS256"],
            audience=config.OIDC_CLIENT_ID, issuer=conf.get("issuer", config.OIDC_ISSUER), leeway=60,
            options={"require": ["exp", "iat"]},
        )
        if claims.get("nonce") != saved["nonce"]:
            raise AuthError("Не совпал одноразовый код входа.")
        if not claims.get(config.OIDC_EMAIL_CLAIM) and tokens.get("access_token") and conf.get("userinfo_endpoint"):
            info = _get_json(conf["userinfo_endpoint"], headers={"Authorization": f"Bearer {tokens['access_token']}"})
            claims = {**info, **claims, config.OIDC_EMAIL_CLAIM: info.get(config.OIDC_EMAIL_CLAIM)}
    except AuthError as e:
        return _error_page(str(e), 502)
    except jwt.PyJWTError as e:
        log.warning("id_token: %s", e)
        return _error_page("Токен входа не прошёл проверку.")
    email = str(claims.get(config.OIDC_EMAIL_CLAIM) or "").strip().lower()
    if not EMAIL_RE.match(email):
        return _error_page(f"В токене нет адреса почты (поле {config.OIDC_EMAIL_CLAIM}). Обратитесь к администратору OpenID.", 403)
    request.session["user"] = {"email": email, "name": str(claims.get(config.OIDC_NAME_CLAIM) or email), "sub": str(claims.get("sub", ""))}
    return RedirectResponse(saved["next"])


@router.get("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    if enabled() and config.OIDC_END_SESSION:
        try:
            end = _conf().get("end_session_endpoint")
        except AuthError:
            end = None
        if end:
            back = str(request.url_for("auth_logged_out"))
            return RedirectResponse(f"{end}?{urlencode({'client_id': config.OIDC_CLIENT_ID, 'post_logout_redirect_uri': back})}")
    return RedirectResponse("/auth/logged-out")


@router.get("/auth/logged-out", name="auth_logged_out")
def auth_logged_out():
    return HTMLResponse("""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вы вышли</title><body style="font-family:Arial,sans-serif;max-width:520px;margin:80px auto;padding:0 16px;color:#1a1a1a">
<h2>Вы вышли из конструктора</h2><p>Чтобы выйти из корпоративной учётной записи полностью, закройте браузер или выйдите на портале.</p>
<p><a href="/auth/login">Войти снова</a></p></body>""")


@router.get("/api/me")
def api_me(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"detail": "Требуется вход"}, status_code=401)
    return {"email": user["email"], "name": user.get("name") or user["email"], "is_admin": is_admin(user), "auth": enabled()}
