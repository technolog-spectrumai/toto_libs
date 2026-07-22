from toto.core.connectors import BaseConnector, ConnectorExecutionError, register_connector

from .client import ApiRequestError, execute_api_request
from .models import Connector as ApiConnectorModel


@register_connector
class ApiRequestConnector(BaseConnector):
    connector_type = "api_request"
    label = "API request"
    app_label = "api"

    def validate(self) -> list[str]:
        has_url = self.config.get("url") or self.config.get("url_field")
        has_api_connector = any(
            self.config.get(key)
            for key in (
                "api_connector_id",
                "api_connector_id_field",
                "api_connector_slug",
                "api_connector_slug_field",
            )
        )
        if not has_url and not has_api_connector:
            return [
                "API request connector requires url/url_field or api_connector_id/api_connector_slug."
            ]
        return []

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        api_connector = self._api_connector(input_data)
        try:
            return self.apply_routes(
                execute_api_request(
                    url=self.configured_value("url", "url_field", input_data, default=None),
                    api_connector=api_connector,
                    endpoint=self._endpoint(input_data),
                    method=self.config.get("method", "GET"),
                    headers=self.configured_value("headers", "headers_field", input_data, default={}),
                    params=self.configured_value("params", "params_field", input_data, default={}),
                    body=self.configured_value("body", "body_field", input_data, default=None),
                    json_payload=self.configured_value("json", "json_field", input_data, default=None),
                    timeout_seconds=self.config.get("timeout_seconds"),
                    max_response_bytes=self.config.get("max_response_bytes"),
                    fail_on_http_error=self.config.get("fail_on_http_error", True),
                    vault_session=None,
                )
            )
        except ApiRequestError as exc:
            raise ConnectorExecutionError(str(exc)) from exc

    def _api_connector(self, input_data: dict) -> ApiConnectorModel | None:
        connector_id = self.configured_value(
            "api_connector_id", "api_connector_id_field", input_data, default=None
        )
        connector_slug = self.configured_value(
            "api_connector_slug", "api_connector_slug_field", input_data, default=None
        )
        if connector_id and connector_slug:
            raise ConnectorExecutionError(
                "API request connector accepts api_connector_id or api_connector_slug, not both."
            )
        try:
            if connector_id:
                return ApiConnectorModel.objects.get(pk=connector_id)
            if connector_slug:
                return ApiConnectorModel.objects.get(slug=connector_slug)
        except ApiConnectorModel.DoesNotExist as exc:
            raise ConnectorExecutionError("Configured API connector was not found.") from exc
        return None

    def _endpoint(self, input_data: dict):
        endpoint = self.configured_value("endpoint", "endpoint_field", input_data, default=None)
        if endpoint is not None:
            return endpoint
        return self.configured_value("path", "path_field", input_data, default=None)
