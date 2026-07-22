"""REST/JSON extractor over ``toto.api.client`` (auth, SSRF guard, size caps).

``extract_config``:

.. code-block:: jsonc

    {
      "endpoint": "works",                 // relative to http_connector.base_url
      "method": "GET",                     // GET | POST
      "params": {"filter": "is_oa:true"},  // static, non-secret
      "headers": {},                       // extra non-secret headers
      "records_path": "results",           // dot-path to the record list; "" = body
      "pagination": {
        "strategy": "none",                // none | page | offset | cursor
        "param": "page", "start": 1,       // page:   param + start (+ size_param/size)
        // "param": "offset",              // offset: param (+ size_param/size);
        //   "size_param": "limit", "size": 100,   //   advances by records returned
        // "param": "cursor",              // cursor: param + next_path (+ initial)
        //   "next_path": "meta.next_cursor", "initial": "*",
        "max_pages": 10
      },
      "max_records": 1000,
      "timeout_seconds": 30                // optional; default connector timeout
    }

Auth headers/params are injected inside ``execute_api_request`` from the
``api.Connector`` — they never appear in this module's composed URLs, so
``FetchPage.url`` is safe to persist in the run's request_log.
"""

from toto.api.client import (
    ApiRequestError,
    _with_query_params,
    build_request_url,
    execute_api_request,
)

from ..mapping import get_path
from . import register
from .base import BaseExtractor, ExtractError, ExtractResult, FetchPage

DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_RECORDS = 1000

_STRATEGIES = ("none", "page", "offset", "cursor")


def _display_url(data_connector, endpoint, params):
    """Compose the pre-auth URL for the audit log (no vault/secret access).

    Uses the same query-merging helper as ``execute_api_request`` so the
    logged URL matches the request actually sent (modulo auth params, which
    are injected later and must never appear here).
    """
    url = build_request_url(api_connector=data_connector.http_connector, endpoint=endpoint)
    return _with_query_params(url, params or {})


@register
class RestApiExtractor(BaseExtractor):
    kind = "rest_api"
    label = "REST/JSON API"

    def validate_config(self, config):
        errors = []
        if not isinstance(config, dict):
            return ["extract_config must be a JSON object."]
        if not isinstance(config.get("endpoint"), str) or not config.get("endpoint").strip():
            errors.append("extract_config.endpoint is required.")
        method = str(config.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            errors.append("extract_config.method must be GET or POST.")
        for key in ("params", "headers"):
            if config.get(key) not in (None, {}) and not isinstance(config.get(key), dict):
                errors.append(f"extract_config.{key} must be an object.")
        if config.get("records_path") is not None and not isinstance(config["records_path"], str):
            errors.append("extract_config.records_path must be a string.")

        pagination = config.get("pagination") or {}
        if not isinstance(pagination, dict):
            errors.append("extract_config.pagination must be an object.")
            pagination = {}
        strategy = pagination.get("strategy") or "none"
        if strategy not in _STRATEGIES:
            errors.append(
                f"pagination.strategy must be one of {', '.join(_STRATEGIES)}."
            )
        if strategy == "cursor" and not isinstance(pagination.get("next_path"), str):
            errors.append("cursor pagination requires a string next_path.")
        if strategy == "offset" and pagination.get("size_param"):
            size = pagination.get("size")
            if not isinstance(size, int) or size <= 0:
                errors.append("offset pagination with size_param requires a positive integer size.")
        for key, owner in (("max_pages", pagination), ("max_records", config),
                           ("timeout_seconds", config)):
            value = owner.get(key)
            if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                errors.append(f"{key} must be a positive number.")
        return errors

    def extract(self, *, data_connector, vault_session=None):
        config = data_connector.extract_config or {}
        errors = self.validate_config(config)
        if errors:
            raise ExtractError("Invalid extract_config: " + " ".join(errors))

        endpoint = config["endpoint"].strip()
        method = str(config.get("method") or "GET").upper()
        headers = dict(config.get("headers") or {})
        static_params = dict(config.get("params") or {})
        records_path = config.get("records_path") or ""
        timeout = config.get("timeout_seconds")

        pagination = config.get("pagination") or {}
        strategy = pagination.get("strategy") or "none"
        max_pages = int(pagination.get("max_pages") or DEFAULT_MAX_PAGES)
        max_records = int(config.get("max_records") or DEFAULT_MAX_RECORDS)

        records, pages = [], []
        capped = False
        cursor = pagination.get("initial") if strategy == "cursor" else None
        page_number = int(pagination.get("start") or 1)
        offset = 0

        for _page_index in range(max_pages if strategy != "none" else 1):
            params = dict(static_params)
            if strategy == "page":
                params[pagination.get("param") or "page"] = page_number
                if pagination.get("size_param") and pagination.get("size"):
                    params[pagination["size_param"]] = pagination["size"]
            elif strategy == "offset":
                params[pagination.get("param") or "offset"] = offset
                if pagination.get("size_param"):
                    params[pagination["size_param"]] = pagination["size"]
            elif strategy == "cursor" and cursor is not None:
                params[pagination.get("param") or "cursor"] = cursor

            page, page_records = self._fetch_page(
                data_connector, endpoint=endpoint, method=method, headers=headers,
                params=params, records_path=records_path, timeout=timeout,
                vault_session=vault_session,
            )
            pages.append(page)
            records.extend(page_records)

            if len(records) >= max_records:
                capped = len(records) > max_records or strategy != "none"
                records = records[:max_records]
                break
            if strategy == "none" or not page_records:
                break
            if strategy == "page":
                page_number += 1
            elif strategy == "offset":
                # Advance by what the server actually returned — a server whose
                # page size is smaller than the configured `size` would
                # otherwise leave silent gaps in the extracted records.
                offset += len(page_records)
            elif strategy == "cursor":
                cursor = get_path(page.json, pagination["next_path"])
                if not cursor:
                    break
        else:
            capped = True  # ran out of max_pages with pages still full

        stats = {"pages": len(pages), "records": len(records), "capped": capped}
        return ExtractResult(records=records, pages=pages, stats=stats)

    def _fetch_page(self, data_connector, *, endpoint, method, headers, params,
                    records_path, timeout, vault_session):
        display_url = _display_url(data_connector, endpoint, params)
        try:
            response = execute_api_request(
                api_connector=data_connector.http_connector,
                endpoint=endpoint,
                method=method,
                headers=headers,
                params=params,
                timeout_seconds=timeout,
                fail_on_http_error=True,
                vault_session=vault_session,
            )
        except ApiRequestError as exc:
            raise ExtractError(f"{display_url}: {exc}") from exc

        data = response.get("data") or {}
        if data.get("truncated"):
            raise ExtractError(
                f"{display_url}: response exceeded the size cap — narrow the query "
                "or paginate with a smaller page size."
            )
        if "json" not in data:
            raise ExtractError(f"{display_url}: response is not JSON.")
        body = data["json"]

        found = get_path(body, records_path) if records_path else body
        if found is None:
            raise ExtractError(
                f"{display_url}: records_path '{records_path}' not found in response."
            )
        if not isinstance(found, list):
            raise ExtractError(
                f"{display_url}: records_path '{records_path}' did not yield a list."
            )

        page = FetchPage(
            url=display_url,
            status_code=int(data.get("status_code") or 0),
            json=body,
            record_count=len(found),
        )
        return page, found
