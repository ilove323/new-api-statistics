"""Run: .venv/bin/python -m unittest discover -s tests."""

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from new_api_statistics import balance
from new_api_statistics import balance_worker
from new_api_statistics.app import app


class BalanceTest(unittest.TestCase):
    def test_next_run_at_ten(self):
        before = datetime(2026, 9, 16, 9, 59, tzinfo=balance.TZ)
        self.assertEqual(
            balance_worker.next_run(before), before.replace(hour=10, minute=0)
        )
        after = datetime(2026, 9, 16, 22, 30, tzinfo=balance.TZ)
        self.assertEqual(
            balance_worker.next_run(after), datetime(2026, 9, 17, 10, tzinfo=balance.TZ)
        )

    def test_worker_sleeps_without_polling_or_startup_check(self):
        with (
            patch("new_api_statistics.balance_worker.Event") as event,
            patch("new_api_statistics.balance_worker.signal.signal"),
            patch("new_api_statistics.balance_worker.datetime") as clock,
            patch("new_api_statistics.balance.configured", return_value=True),
            patch("new_api_statistics.balance.initialize"),
            patch("new_api_statistics.balance.check_once") as check,
        ):
            clock.now.return_value = datetime(2026, 9, 16, 9, tzinfo=balance.TZ)
            event.return_value.is_set.return_value = False
            event.return_value.wait.return_value = True
            balance_worker.main()
            event.return_value.wait.assert_called_once_with(3600)
            check.assert_not_called()

    def test_manual_check_endpoint(self):
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch("new_api_statistics.balance.check_once", return_value=True) as check,
            patch(
                "new_api_statistics.balance.snapshot", return_value={"configured": True}
            ) as snapshot,
        ):
            client = app.test_client()
            url = "/statistics/api/balance/check"
            self.assertEqual(
                client.post(url, json={}, auth=("test_admin", "test")).status_code, 403
            )
            check.assert_not_called()
            response = client.post(
                url,
                json={},
                auth=("test_admin", "test"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(response.status_code, 200)
            check.assert_called_once_with(daily=False)
            snapshot.assert_called_once_with(live=False)
            check.side_effect = balance.CheckBusy()
            self.assertEqual(
                client.post(
                    url,
                    json={},
                    auth=("test_admin", "test"),
                    headers={"X-Statistics-Request": "1"},
                ).status_code,
                409,
            )

    def body(self, **values):
        return dict(
            dict(
                budget="100.00",
                threshold="10",
                start_month="2026-01",
                enabled=True,
                version=1,
            ),
            **values,
        )

    def test_validation(self):
        result = balance.validate_settings(
            self.body(excluded_channel_ids=[7, 2]), date(2026, 2, 10)
        )
        self.assertEqual(result["budget"], Decimal(100))
        self.assertEqual(result["start_month"], date(2026, 1, 1))
        self.assertEqual(result["excluded_channel_ids"], [2, 7])
        for changes in [
            dict(budget="NaN"),
            dict(threshold="Infinity"),
            dict(budget="-1"),
            dict(budget="1.001"),
            dict(enabled="true"),
            dict(version=True),
            dict(start_month="2026-03"),
            dict(start_month="2026-1"),
            dict(excluded_channel_ids="2"),
            dict(excluded_channel_ids=[True]),
            dict(excluded_channel_ids=[2, 2]),
            dict(excluded_channel_ids=[-1]),
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                balance.validate_settings(self.body(**changes), date(2026, 2, 10))

    def test_usage_channels_endpoint(self):
        rows = [
            {
                "channel_id": 1,
                "channel_name": "主渠道",
                "channel_status": 1,
                "included": True,
            }
        ]
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch(
                "new_api_statistics.balance.usage_channels_snapshot",
                return_value=rows,
            ),
        ):
            response = app.test_client().get(
                "/statistics/api/balance/usage-channels",
                auth=("test_admin", "test"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"rows": rows})

    def test_recalculate_history_endpoint_and_fail_closed(self):
        client = app.test_client()
        body = self.body(excluded_channel_ids=[2])
        preview_rows = [{"month": "2026-01", "before": "10.00", "after": "8.00"}]
        result = {"version": 2, "months": 3}
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch(
                "new_api_statistics.balance.history_preview",
                return_value={"version": 1, "rows": preview_rows},
            ) as preview,
            patch(
                "new_api_statistics.balance.recalculate_history",
                return_value=result,
            ) as recalculate,
        ):
            url = "/statistics/api/balance/recalculate-history"
            preview_url = url + "/preview"
            self.assertEqual(
                client.post(
                    preview_url, json=body, auth=("test_admin", "test")
                ).status_code,
                403,
            )
            preview_response = client.post(
                preview_url,
                json=body,
                auth=("test_admin", "test"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(
                preview_response.get_json(), {"version": 1, "rows": preview_rows}
            )
            preview.assert_called_once_with(body)
            payload = {"settings": body, "preview": preview_rows}
            response = client.post(
                url,
                json=payload,
                auth=("test_admin", "test"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(
                response.get_json(),
                {"recalculated": True, "version": 2, "months": 3},
            )
            recalculate.assert_called_once_with(body, preview_rows, "test_admin")
            recalculate.side_effect = balance.HistoryPreviewChanged()
            failed = client.post(
                url,
                json=payload,
                auth=("test_admin", "test"),
                headers={"X-Statistics-Request": "1"},
            )
            self.assertEqual(failed.status_code, 409)
            self.assertIn("重新预览", failed.get_json()["error"])

    def test_month_boundaries(self):
        self.assertEqual(
            balance.month_list(date(2025, 12, 1), date(2026, 3, 1)),
            [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)],
        )

    def test_api_writes_require_auth_and_custom_header(self):
        client = app.test_client()
        with patch("new_api_statistics.app.verify_admin", return_value=False):
            self.assertEqual(
                client.put(
                    "/statistics/api/balance/settings", json=self.body()
                ).status_code,
                401,
            )
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch("new_api_statistics.balance.save_settings") as save,
        ):
            args = dict(auth=("test_admin", "test"), json=self.body())
            self.assertEqual(
                client.put("/statistics/api/balance/settings", **args).status_code, 403
            )
            self.assertEqual(
                client.put(
                    "/statistics/api/balance/settings",
                    headers={
                        "X-Statistics-Request": "1",
                        "Sec-Fetch-Site": "cross-site",
                    },
                    **args,
                ).status_code,
                403,
            )
            save.assert_not_called()
            self.assertEqual(
                client.put(
                    "/statistics/api/balance/settings",
                    headers={"X-Statistics-Request": "1"},
                    **args,
                ).status_code,
                200,
            )
            save.assert_called_once_with(self.body(), "test_admin")
            save.side_effect = balance.SettingsConflict()
            self.assertEqual(
                client.put(
                    "/statistics/api/balance/settings",
                    headers={"X-Statistics-Request": "1"},
                    **args,
                ).status_code,
                409,
            )

    def test_unconfigured(self):
        with patch.dict("os.environ", {"MONITOR_DATABASE_URL": ""}):
            self.assertEqual(balance.snapshot(), {"configured": False})


if __name__ == "__main__":
    unittest.main()
