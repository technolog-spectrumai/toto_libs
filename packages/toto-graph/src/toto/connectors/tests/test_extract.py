from unittest.mock import patch

from django.test import TestCase

from toto.connectors.services.extract import ExtractError, get_extractor
from toto.connectors.services.extract.rest import RestApiExtractor

from .helpers import make_data_connector


def _resp(json_body, status=200, truncated=False, with_json=True):
    data = {
        "status_code": status,
        "headers": {},
        "body": "",
        "truncated": truncated,
    }
    if with_json:
        data["json"] = json_body
    return {"data": data}


def _config(**overrides):
    config = {
        "endpoint": "items",
        "method": "GET",
        "records_path": "results",
        "pagination": {"strategy": "none"},
    }
    config.update(overrides)
    return config


class RegistryTests(TestCase):
    def test_rest_is_registered(self):
        self.assertIsInstance(get_extractor("rest_api"), RestApiExtractor)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ExtractError):
            get_extractor("carrier-pigeon")


class ValidateConfigTests(TestCase):
    def setUp(self):
        self.extractor = RestApiExtractor()

    def test_valid(self):
        self.assertEqual(self.extractor.validate_config(_config()), [])

    def test_endpoint_required(self):
        self.assertTrue(any("endpoint" in e for e in self.extractor.validate_config({})))

    def test_method_restricted(self):
        errors = self.extractor.validate_config(_config(method="DELETE"))
        self.assertTrue(any("GET or POST" in e for e in errors))

    def test_cursor_needs_next_path(self):
        errors = self.extractor.validate_config(
            _config(pagination={"strategy": "cursor", "param": "c"})
        )
        self.assertTrue(any("next_path" in e for e in errors))

    def test_offset_with_size_param_needs_size(self):
        errors = self.extractor.validate_config(
            _config(pagination={"strategy": "offset", "param": "o", "size_param": "limit"})
        )
        self.assertTrue(any("size" in e for e in errors))

    def test_offset_without_size_param_is_valid(self):
        errors = self.extractor.validate_config(
            _config(pagination={"strategy": "offset", "param": "o"})
        )
        self.assertEqual(errors, [])


@patch("toto.connectors.services.extract.rest.execute_api_request")
class ExtractTests(TestCase):
    def setUp(self):
        self.connector = make_data_connector()

    def _extract(self, api, config, pages):
        api.side_effect = pages
        self.connector.extract_config = config
        return RestApiExtractor().extract(data_connector=self.connector)

    def test_single_page(self, api):
        result = self._extract(api, _config(), [_resp({"results": [{"a": 1}, {"a": 2}]})])
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.stats, {"pages": 1, "records": 2, "capped": False})
        self.assertEqual(api.call_count, 1)

    def test_body_as_list_with_empty_records_path(self, api):
        result = self._extract(api, _config(records_path=""), [_resp([{"a": 1}])])
        self.assertEqual(len(result.records), 1)

    def test_page_pagination_stops_on_empty_page(self, api):
        config = _config(pagination={"strategy": "page", "param": "page", "start": 1})
        result = self._extract(api, config, [
            _resp({"results": [{"a": 1}]}),
            _resp({"results": [{"a": 2}]}),
            _resp({"results": []}),
        ])
        self.assertEqual(len(result.records), 2)
        self.assertEqual(api.call_count, 3)
        sent_pages = [call.kwargs["params"]["page"] for call in api.call_args_list]
        self.assertEqual(sent_pages, [1, 2, 3])

    def test_offset_pagination_advances_by_records_returned(self, api):
        # The server ignores/clamps the requested size — offset must follow
        # what actually came back, or records would be silently skipped.
        config = _config(pagination={
            "strategy": "offset", "param": "offset",
            "size_param": "limit", "size": 100,
        })
        self._extract(api, config, [
            _resp({"results": [{"a": 1}, {"a": 2}]}),   # server page size: 2
            _resp({"results": [{"a": 3}]}),
            _resp({"results": []}),
        ])
        offsets = [call.kwargs["params"]["offset"] for call in api.call_args_list]
        self.assertEqual(offsets, [0, 2, 3])
        limits = [call.kwargs["params"]["limit"] for call in api.call_args_list]
        self.assertEqual(limits, [100, 100, 100])

    def test_cursor_pagination_follows_next_and_stops(self, api):
        config = _config(pagination={
            "strategy": "cursor", "param": "cursor",
            "next_path": "meta.next", "initial": "*",
        })
        self._extract(api, config, [
            _resp({"results": [{"a": 1}], "meta": {"next": "c2"}}),
            _resp({"results": [{"a": 2}], "meta": {"next": None}}),
        ])
        cursors = [call.kwargs["params"]["cursor"] for call in api.call_args_list]
        self.assertEqual(cursors, ["*", "c2"])

    def test_max_records_caps(self, api):
        config = _config(
            max_records=3,
            pagination={"strategy": "page", "param": "page", "start": 1},
        )
        result = self._extract(api, config, [
            _resp({"results": [{"a": 1}, {"a": 2}]}),
            _resp({"results": [{"a": 3}, {"a": 4}]}),
        ])
        self.assertEqual(len(result.records), 3)
        self.assertTrue(result.stats["capped"])

    def test_max_pages_caps(self, api):
        config = _config(pagination={"strategy": "page", "param": "page", "max_pages": 2})
        result = self._extract(api, config, [
            _resp({"results": [{"a": 1}]}),
            _resp({"results": [{"a": 2}]}),
        ])
        self.assertEqual(api.call_count, 2)
        self.assertTrue(result.stats["capped"])

    def test_non_json_response_raises(self, api):
        with self.assertRaises(ExtractError):
            self._extract(api, _config(), [_resp(None, with_json=False)])

    def test_truncated_response_raises(self, api):
        with self.assertRaises(ExtractError):
            self._extract(api, _config(), [_resp({"results": []}, truncated=True)])

    def test_missing_records_path_raises(self, api):
        with self.assertRaises(ExtractError):
            self._extract(api, _config(records_path="nope"), [_resp({"results": []})])

    def test_records_path_must_be_list(self, api):
        with self.assertRaises(ExtractError):
            self._extract(api, _config(records_path="results"), [_resp({"results": {"a": 1}})])

    def test_urls_are_auth_free(self, api):
        config = _config(params={"q": "x"})
        result = self._extract(api, config, [_resp({"results": []})])
        url = result.pages[0].url
        self.assertTrue(url.startswith("https://api.example.com/items"))
        self.assertIn("q=x", url)
        for needle in ("key", "token", "secret", "authorization"):
            self.assertNotIn(needle, url.lower())
