import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.conf import settings

from .models import ApiConnector


class ApiRequestError(RuntimeError):
    pass


SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def execute_api_request(
    *,
    url: str | None = None,
    api_connector: ApiConnector | None = None,
    endpoint: str | None = None,
    method: str = "GET",
    headers: dict | None = None,
    params: dict | None = None,
    body: Any = None,
    json_payload: Any = None,
    timeout_seconds: int | float | None = None,
    max_response_bytes: int | None = None,
    fail_on_http_error: bool = True,
    vault_session=None,
) -> dict:
    """Execute an outbound API request using the shared API connector model."""
    request_url = build_request_url(
        url=url,
        api_connector=api_connector,
        endpoint=endpoint,
    )
    method = _request_method(method)
    headers = _coerce_object(headers or {}, "headers")
    params = _coerce_object(params or {}, "params")

    if api_connector is not None:
        extra_headers = _connector_extra_headers(api_connector)
        try:
            auth_headers = api_connector.build_auth_headers(vault_session=vault_session)
            auth_params = api_connector.build_auth_query_params(vault_session=vault_session)
        except RuntimeError as exc:
            raise ApiRequestError(str(exc)) from exc
        headers = {**extra_headers, **headers, **auth_headers}
        params = {**params, **auth_params}

    request_url = _with_query_params(request_url, params)
    headers = {str(key): str(value) for key, value in headers.items()}
    body_bytes = _request_body(body=body, json_payload=json_payload, headers=headers)

    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else (
            api_connector.get_timeout()
            if api_connector is not None
            else getattr(settings, "WORKFLOW_API_CONNECTOR_TIMEOUT_SECONDS", 30)
        )
    )
    max_bytes = int(
        max_response_bytes
        if max_response_bytes is not None
        else getattr(settings, "WORKFLOW_API_CONNECTOR_MAX_RESPONSE_BYTES", 1024 * 1024)
    )

    request = Request(request_url, data=body_bytes, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return _response_data(response, max_bytes)
    except HTTPError as exc:
        if fail_on_http_error:
            body_text = exc.read(max_bytes).decode("utf-8", errors="replace")
            raise ApiRequestError(f"API request failed with HTTP {exc.code}: {body_text}") from exc
        return _response_data(exc, max_bytes)
    except URLError as exc:
        raise ApiRequestError(f"API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ApiRequestError(f"API request timed out after {timeout:g} seconds.") from exc


def build_request_url(
    *,
    url: str | None = None,
    api_connector: ApiConnector | None = None,
    endpoint: str | None = None,
) -> str:
    if api_connector is not None:
        if not api_connector.is_active:
            raise ApiRequestError(f'API connector "{api_connector.name}" is inactive.')
        if not api_connector.base_url:
            raise ApiRequestError(f'API connector "{api_connector.name}" has no base_url configured.')
        target = endpoint if endpoint is not None else (url or "")
        request_url = _join_base_url(api_connector.base_url, target)
    else:
        request_url = url or endpoint or ""

    parsed = urlparse(str(request_url))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ApiRequestError("API request URL must be an absolute http(s) URL.")

    allowed_hosts = getattr(settings, "API_CONNECTOR_ALLOWED_HOSTS", None)
    if allowed_hosts is None:
        allowed_hosts = getattr(settings, "WORKFLOW_API_CONNECTOR_ALLOWED_HOSTS", None)
    if allowed_hosts and parsed.hostname not in _allowed_host_set(allowed_hosts):
        raise ApiRequestError(f"API host {parsed.hostname!r} is not allowed.")

    return urlunparse(parsed)


def _join_base_url(base_url: str, endpoint: str | None) -> str:
    endpoint = str(endpoint or "")
    endpoint_parts = urlparse(endpoint)
    if endpoint_parts.scheme or endpoint_parts.netloc:
        raise ApiRequestError("API connector endpoint must be relative to its base_url.")
    if endpoint.startswith("?"):
        return f"{base_url}{endpoint}"
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(base, endpoint.lstrip("/"))


def _request_method(method: str) -> str:
    method = str(method or "GET").upper()
    if method not in SUPPORTED_METHODS:
        raise ApiRequestError(f"Unsupported API method: {method}")
    return method


def _coerce_object(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ApiRequestError(f"API request {name} must be an object.")
    return value


def _connector_extra_headers(api_connector: ApiConnector) -> dict:
    extra = api_connector.extra or {}
    headers = extra.get("headers") or extra.get("default_headers") or {}
    if not isinstance(headers, dict):
        raise ApiRequestError(f'API connector "{api_connector.name}" extra headers must be an object.')
    return headers


def _with_query_params(url: str, params: dict) -> str:
    if not params:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _request_body(*, body, json_payload, headers: dict) -> bytes | None:
    if json_payload is not None:
        _ensure_header(headers, "Content-Type", "application/json")
        return json.dumps(json_payload).encode("utf-8")
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    _ensure_header(headers, "Content-Type", "application/json")
    return json.dumps(body).encode("utf-8")


def _ensure_header(headers: dict, name: str, value: str) -> None:
    if not any(str(key).lower() == name.lower() for key in headers):
        headers[name] = value


def _response_data(response, max_bytes: int) -> dict:
    raw = response.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    body = raw.decode("utf-8", errors="replace")
    data = {
        "status_code": _response_status(response),
        "headers": _response_headers(response),
        "body": body,
        "truncated": truncated,
    }
    try:
        data["json"] = json.loads(body)
    except ValueError:
        pass
    return {"data": data}


def _response_status(response) -> int:
    status = getattr(response, "status", None)
    if status is not None:
        return status
    code = getattr(response, "code", None)
    if code is not None:
        return code
    return response.getcode()


def _response_headers(response) -> dict:
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "items"):
        return dict(headers.items())
    return dict(headers)


def _allowed_host_set(allowed_hosts) -> set[str]:
    if isinstance(allowed_hosts, str):
        return {host.strip() for host in allowed_hosts.split(",") if host.strip()}
    return {str(host) for host in allowed_hosts}
