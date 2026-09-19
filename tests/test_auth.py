"""Cover New API bcrypt passwords and administrator status checks."""

import unittest
from unittest.mock import patch
import bcrypt
from new_api_statistics.auth import verify_admin, verify_api_key
from new_api_statistics.app import app


class AuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashed = bcrypt.hashpw(
            "测试Password".encode(), bcrypt.gensalt(rounds=4, prefix=b"2a")
        ).decode()

    def user(self, **changes):
        return dict(
            id=1,
            username="test_admin",
            password=self.hashed,
            **dict({"role": 100, "status": 1, "deleted_at": None}, **changes),
        )

    def test_root_and_admin(self):
        for role in (10, 100):
            with patch(
                "new_api_statistics.auth.find_user", return_value=self.user(role=role)
            ):
                self.assertTrue(verify_admin("test_admin", "测试Password"))
                self.assertFalse(verify_admin("test_admin", "wrong"))

    def test_disabled_deleted_and_ordinary_users(self):
        for changes in ({"role": 1}, {"status": 2}, {"deleted_at": "2026-01-01"}):
            with patch(
                "new_api_statistics.auth.find_user", return_value=self.user(**changes)
            ):
                self.assertFalse(verify_admin("test_admin", "测试Password"))

    def test_missing_and_malformed(self):
        with patch("new_api_statistics.auth.find_user", return_value=None):
            self.assertFalse(verify_admin("unknown", "testing"))
        with patch(
            "new_api_statistics.auth.find_user",
            return_value={**self.user(), "password": "invalid"},
        ):
            self.assertFalse(verify_admin("test_admin", "testing"))
        self.assertFalse(verify_admin("", "testing"))
        self.assertFalse(verify_admin("test_admin", "a" * 73))

    def test_api_key_uses_new_api_token_table_and_sk_prefix(self):
        with patch("new_api_statistics.auth.psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value = {"id": 12}
            self.assertTrue(verify_api_key("sk-fixture"))
            query, params = conn.execute.call_args.args
            self.assertIn("FROM tokens", query)
            self.assertIn("JOIN users", query)
            self.assertIn("t.expired_time", query)
            self.assertEqual(params[0], "fixture")
            conn.execute.return_value.fetchone.return_value = None
            self.assertFalse(verify_api_key("fixture"))

    def test_api_key_rejects_malformed_without_database_query(self):
        with patch("new_api_statistics.auth.psycopg.connect") as connect:
            for value in ("", None, "sk-", "x" * 257):
                self.assertFalse(verify_api_key(value))
            connect.assert_not_called()

    def test_bearer_cannot_access_web_admin_endpoints(self):
        with patch("new_api_statistics.app.verify_api_key", return_value=True):
            for route in (
                "/statistics/api/balance/status",
                "/statistics/api/balance/settings",
                "/statistics/api/usage",
            ):
                response = app.test_client().get(
                    route, headers={"Authorization": "Bearer fixture"}
                )
                self.assertEqual(response.status_code, 401)

    def test_page_and_all_protected_routes(self):
        client = app.test_client()
        with (
            patch("new_api_statistics.auth.find_user", return_value=self.user()),
            patch("new_api_statistics.app.load_site_name", return_value="测试站点"),
        ):
            self.assertEqual(
                client.get(
                    "/statistics/", auth=("test_admin", "测试Password")
                ).status_code,
                200,
            )
        for route in (
            "/statistics/",
            "/statistics/static/app.js",
            "/statistics/api/usage",
            "/statistics/api/usage/by-token",
            "/statistics/api/usage/tokens",
            "/statistics/api/usage/groups",
            "/statistics/api/usage/by-selection",
            "/statistics/api/export",
            "/statistics/api/balance/usage-channels",
        ):
            self.assertEqual(client.get(route).status_code, 401)
            with patch(
                "new_api_statistics.auth.find_user", return_value=self.user(role=1)
            ):
                self.assertEqual(
                    client.get(route, auth=("test_admin", "测试Password")).status_code,
                    401,
                )


if __name__ == "__main__":
    unittest.main()
