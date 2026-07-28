import unittest

from flask import Flask

from test_plan_viewer.web.platform_records import (
    EMPTY_RECORD_BUCKETS,
    PlatformRecordServices,
    create_platform_records_blueprint,
    serialize_platform_record_buckets,
)


def make_app(services):
    application = Flask(__name__)
    application.register_blueprint(create_platform_records_blueprint(services))
    return application


class PlatformRecordRouteTests(unittest.TestCase):
    def test_disabled_database_returns_complete_empty_contract(self):
        services = PlatformRecordServices(
            get_database_config=lambda: {"enabled": False},
            load_records=lambda: self.fail("disabled persistence must not load records"),
            save_record=lambda _bucket, _key, _record: None,
        )

        with make_app(services).test_client() as client:
            response = client.get("/api/platform-records")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["enabled"])
        self.assertEqual(response.json["records"], EMPTY_RECORD_BUCKETS)

    def test_enabled_database_serializes_singleton_and_keyed_buckets(self):
        buckets = {
            name: {"default": {"bucket": name}}
            if name in {"view_state", "test_suites"}
            else {"record": {"bucket": name}}
            for name in EMPTY_RECORD_BUCKETS
        }
        services = PlatformRecordServices(
            get_database_config=lambda: {"enabled": True},
            load_records=lambda: buckets,
            save_record=lambda _bucket, _key, _record: None,
        )

        with make_app(services).test_client() as client:
            response = client.get("/api/platform-records")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["records"], serialize_platform_record_buckets(buckets))
        self.assertIsNone(response.json["error"])

    def test_save_validates_body_and_maps_service_errors(self):
        calls = []

        def save_record(bucket, key, record):
            calls.append((bucket, key, record))
            if bucket == "invalid":
                raise ValueError("invalid bucket")

        services = PlatformRecordServices(
            get_database_config=lambda: {"enabled": False},
            load_records=lambda: {},
            save_record=save_record,
        )

        with make_app(services).test_client() as client:
            invalid_body = client.put("/api/platform-records/view_state/default", json={"record": []})
            invalid_bucket = client.put("/api/platform-records/invalid/default", json={"record": {}})
            saved = client.put(
                "/api/platform-records/view_state/a%2Fb",
                json={"record": {"activeSection": "plans"}},
            )

        self.assertEqual(invalid_body.status_code, 400)
        self.assertEqual(invalid_bucket.status_code, 400)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            calls[-1],
            ("view_state", "a/b", {"activeSection": "plans"}),
        )


if __name__ == "__main__":
    unittest.main()
