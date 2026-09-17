"""Run: .venv/bin/python -m unittest discover -s tests."""

import unittest
from io import BytesIO
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook

from new_api_statistics.app import app
from new_api_statistics.report import (
    current_prices,
    decorate,
    export_excel,
    period,
    totals,
    rankings,
    TOKEN_FIELDS,
    convert_tokens,
    load_site_name,
    cost_formula,
)


class ReportTest(unittest.TestCase):
    def test_cost_formula_decimal_and_difference(self):
        row = dict(
            input_tokens=1000000,
            output_tokens=200000,
            cache_read_tokens=300000,
            cache_write_tokens=100000,
            input_price=Decimal(2),
            output_price=Decimal(10),
            cache_price=Decimal(".2"),
            write_price=Decimal("2.5"),
            group_ratio=Decimal(3),
            amount=Decimal("13"),
            ratio_count=2,
            converted_cache_read_tokens=None,
        )
        result = cost_formula(row)
        self.assertEqual(result["calculated"], Decimal("12.93"))
        self.assertEqual(result["difference"], Decimal(".07"))
        row["input_price"] = None
        self.assertIsNone(cost_formula(row)["calculated"])
        row["input_tokens"] = 0
        self.assertEqual(cost_formula(row)["calculated"], Decimal("6.93"))
        row["converted_cache_read_tokens"] = 0
        self.assertTrue(cost_formula(row)["converted"])

    def test_site_name_database_and_fallback(self):
        with patch("new_api_statistics.report.psycopg.connect") as connect:
            conn = connect.return_value.__enter__.return_value
            for value, expected in [
                (("示例网关",), "示例网关"),
                (None, "New API"),
                (("",), "New API"),
            ]:
                conn.execute.return_value.fetchone.return_value = value
                self.assertEqual(load_site_name(), expected)
            self.assertEqual(conn.execute.call_args.args[1], ("SystemName",))

    def test_site_name_rendered_and_escaped(self):
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch(
                "new_api_statistics.app.load_site_name", return_value="示例网关<script>"
            ),
        ):
            response = app.test_client().get("/statistics/", auth=("admin", "test"))
            text = response.get_data(as_text=True)
            self.assertIn("示例网关&lt;script&gt;", text)
            self.assertIn("用量统计</title>", text)
            self.assertIn("令牌汇总", text)
            self.assertIn("分令牌", text)
            self.assertIn("data-token-column hidden", text)
            self.assertIn('id="token-filter"', text)
            self.assertIn("筛选令牌", text)
            self.assertIn('id="group-filter"', text)
            self.assertIn("筛选分组", text)
            self.assertIn('data-preset="current-month">本月', text)
            script = Path(app.static_folder, "app.js").read_text()
            self.assertIn("filter-search", script)
            self.assertIn("row.style.display=matched?'':'none'", script)
            self.assertIn("输入关键字筛选", script)
            presets = Path(app.static_folder, "presets.js").read_text()
            self.assertIn("preset === 'current-month'", presets)
            stylesheet = Path(app.static_folder, "app.css").read_text()
            self.assertIn(".filter-menu label[hidden]{display:none}", stylesheet)

    def setUp(self):
        self.rows = decorate(
            [
                dict(
                    user_id=1,
                    username="=danger",
                    display_name="测试显示名",
                    model_name="claude-a",
                    total_tokens=100,
                    request_count=2,
                    input_tokens=10,
                    output_tokens=20,
                    cache_write_tokens=30,
                    cache_read_tokens=40,
                    amount=Decimal("10"),
                    group_ratio=Decimal("3.8"),
                ),
                dict(
                    user_id=1,
                    username="=danger",
                    display_name="测试显示名",
                    model_name="gpt-b",
                    total_tokens=900,
                    request_count=4,
                    input_tokens=800,
                    output_tokens=100,
                    cache_write_tokens=0,
                    cache_read_tokens=500,
                    amount=Decimal("1"),
                    group_ratio=Decimal("1"),
                ),
            ],
            {},
        )

    def test_period_inclusive_shanghai(self):
        self.assertEqual(period("2026-07-26", "2026-08-25"), (1784995200, 1787673600))
        with self.assertRaises(ValueError):
            period("2026-08-25", "2026-07-26")
        with self.assertRaises(ValueError):
            period("bad", "2026-08-25")

    def test_token_breakdown(self):
        result = totals(self.rows)
        self.assertEqual([result[k] for k in TOKEN_FIELDS], [810, 120, 540, 30])
        self.assertEqual(result["total_tokens"], 1000)
        self.assertEqual(
            set(result),
            {
                *TOKEN_FIELDS,
                "total_tokens",
                "amount",
                "users",
                "models",
                "request_count",
            },
        )

    def test_interval_average_rates(self):
        start, end = "2026-09-01T00:00:00", "2026-09-01T00:01:59"
        result = totals(self.rows, start, end)
        self.assertEqual(result["duration_seconds"], 120)
        self.assertEqual(result["tpm"], 500)
        self.assertEqual(result["rpm"], 3)
        single = totals(self.rows[:1], start, end)
        self.assertEqual(single["tpm"], 50)
        self.assertEqual(single["rpm"], 1)
        empty = totals([], start, end)
        self.assertEqual((empty["tpm"], empty["rpm"]), (0, 0))
        second = totals(self.rows, start, start)
        self.assertEqual((second["tpm"], second["rpm"]), (60000, 360))
        day = totals(self.rows, "2026-09-01", "2026-09-01")
        self.assertEqual(day["duration_seconds"], 86400)

    def test_rankings_aggregate_users_before_sorting(self):
        other = dict(
            self.rows[0],
            user_id=2,
            username="other",
            amount=Decimal("20"),
            total_tokens=50,
        )
        result = rankings([*self.rows, other])
        self.assertEqual(
            [r["username"] for r in result["user_tokens"]], ["=danger", "other"]
        )
        self.assertEqual(
            [r["username"] for r in result["user_amount"]], ["other", "=danger"]
        )
        self.assertEqual(result["user_tokens"][0]["total_tokens"], 1000)
        self.assertEqual(result["user_tokens"][0]["display_name"], "测试显示名")
        self.assertEqual(result["model_amount"][0]["amount"], 30)

    def test_time_boundaries(self):
        a, b = period("2026-07-26T18:20:30", "2026-07-26T18:20:30")
        self.assertEqual(b - a, 1)
        self.assertEqual(a, 1784995200 + 18 * 3600 + 20 * 60 + 30)
        self.assertEqual(period("2026-07-26 18:20", "2026-07-26T18:21:00")[1] - a, 31)
        with self.assertRaises(ValueError):
            period("2026-07-26T19:00:00", "2026-07-26T18:59:59")
        with self.assertRaises(ValueError):
            period("2026-07-26T19:00:00Z", "2026-07-27")

    def test_excel_exact_time(self):
        ws = load_workbook(
            export_excel([], "2026-07-26T18:20:30", "2026-07-26T19:21:31")
        ).active
        self.assertEqual(
            ws["B1"].value, "2026-07-26 18:20:30 至 2026-07-26 19:21:31（北京时间）"
        )

    def test_prices_missing_not_zero(self):
        options = {
            "ModelRatio": {"claude-a": 5},
            "CompletionRatio": {"claude-a": 5},
            "CacheRatio": {"claude-a": 0.1},
        }
        self.assertEqual(
            current_prices("claude-a", options),
            dict(input_price=10, output_price=50, cache_price=1, write_price=None),
        )

    def test_excel_numbers_merge_formulas_and_literal_names(self):
        ws = load_workbook(export_excel(self.rows, "2026-07-26", "2026-08-25")).active
        self.assertIn("A3:A4", str(ws.merged_cells))
        self.assertIn("B3:B4", str(ws.merged_cells))
        self.assertEqual(ws["A3"].data_type, "s")
        self.assertEqual(ws["A3"].value, "=danger")
        self.assertEqual(ws["B2"].value, "显示名")
        self.assertEqual(ws["B3"].value, "测试显示名")
        self.assertEqual(ws["C2"].value, "消费请求数")
        self.assertEqual(ws["J2"].value, "倍率")
        self.assertEqual(ws["D3"].data_type, "s")
        self.assertEqual(ws["O3"].data_type, "n")
        self.assertEqual(ws["O5"].value, "=SUM(O3:O4)")
        for col in "CEFGHIO":
            self.assertEqual(ws[f"{col}3"].data_type, "n")
            self.assertEqual(ws[f"{col}5"].value, f"=SUM({col}3:{col}4)")
        self.assertEqual(ws.max_column, 15)
        self.assertNotIn("计价非缓存输入Token", [c.value for c in ws[2]])
        self.assertNotIn("折算状态", [c.value for c in ws[2]])

    def test_merged_conversion_used_consistently(self):
        original = dict(
            user_id=2,
            username="converted",
            model_name="gpt",
            request_count=2,
            total_tokens=10000,
            input_tokens=10000,
            output_tokens=0,
            cache_read_tokens=10000,
            cache_write_tokens=0,
            pricing_input_tokens=0,
            ratio_count=2,
            group_ratio=Decimal("3.4"),
            amount=Decimal(".032"),
        )
        options = {"ModelRatio": {"gpt": 1}, "CacheRatio": {"gpt": 0.5}}
        row = decorate([deepcopy(original)], options)[0]
        self.assertEqual(row["total_tokens"], row["converted_total_tokens"])
        self.assertEqual(row["cache_read_tokens"], row["converted_cache_read_tokens"])
        self.assertEqual(row["original_total_tokens"], 10000)
        self.assertEqual(row["original_cache_read_tokens"], 10000)
        self.assertIsInstance(row["cache_read_tokens"], int)
        self.assertIsInstance(row["total_tokens"], int)
        self.assertEqual(row["cache_read_tokens"], 9412)
        self.assertEqual(totals([row])["total_tokens"], row["total_tokens"])
        self.assertEqual(
            rankings([row])["user_tokens"][0]["total_tokens"], row["total_tokens"]
        )
        ws = load_workbook(export_excel([row], "2026-09-01", "2026-09-01")).active
        self.assertAlmostEqual(ws["E3"].value, float(row["total_tokens"]))
        self.assertAlmostEqual(ws["H3"].value, float(row["cache_read_tokens"]))
        self.assertEqual(ws["E3"].number_format, "#,##0")
        self.assertEqual(ws["H3"].number_format, "#,##0")
        self.assertEqual(ws["E2"].value, "总Token")
        self.assertAlmostEqual(ws["O3"].value, float(row["amount"]))
        self.assertNotIn("折算总Token", [c.value for c in ws[2]])
        for count in (1, 3):
            fallback = decorate([dict(original, ratio_count=count)], options)[0]
            self.assertEqual(fallback["total_tokens"], 10000)
        zero = decorate([dict(original, amount=Decimal(0))], options)[0]
        self.assertEqual(zero["total_tokens"], 0)

    def test_conversion_unique_solution_preserves_original(self):
        row = dict(
            ratio_count=2,
            group_ratio=Decimal("3.4"),
            amount=Decimal("0.032"),
            pricing_input_tokens=0,
            output_tokens=0,
            cache_write_tokens=0,
            cache_read_tokens=10000,
            total_tokens=10000,
            input_price=None,
            output_price=None,
            write_price=None,
            cache_price=Decimal(1),
        )
        convert_tokens(row)
        self.assertLess(row["converted_cache_read_tokens"], 10000)
        self.assertEqual(row["cache_read_tokens"], 10000)
        self.assertEqual(
            (
                row["converted_cache_read_tokens"]
                * row["cache_price"]
                * row["group_ratio"]
                / 1000000
            ).quantize(Decimal(".000001")),
            row["amount"],
        )
        row["group_ratio"] = Decimal(3)
        convert_tokens(row)
        self.assertGreater(row["converted_cache_read_tokens"], 10000)
        self.assertEqual(
            row["converted_total_tokens"], row["converted_cache_read_tokens"]
        )
        for count in (1, 3):
            row["ratio_count"] = count
            convert_tokens(row)
            self.assertIsNone(row["converted_total_tokens"])
        row.update(ratio_count=2, pricing_input_tokens=100000, input_price=Decimal(10))
        convert_tokens(row)
        self.assertIsNone(row["converted_total_tokens"])
        row["cache_price"] = None
        convert_tokens(row)
        self.assertIsNone(row["converted_total_tokens"])

    def test_excel_ranking_sheets(self):
        wb = load_workbook(export_excel(self.rows, "2026-07-26", "2026-08-25"))
        self.assertEqual(
            wb.sheetnames,
            ["用户模型用量", "模型消费", "用户Token用量", "用户消费", "区间汇总"],
        )
        self.assertEqual(wb["用户Token用量"]["C3"].value, "测试显示名")
        self.assertEqual(wb["用户Token用量"]["D3"].value, 1000)
        self.assertEqual(wb["用户消费"]["C3"].value, "测试显示名")
        self.assertEqual(wb["用户消费"]["D3"].value, 11)
        self.assertEqual(wb["用户消费"]["B3"].data_type, "s")
        self.assertEqual(wb["用户消费"]["C3"].data_type, "s")
        self.assertEqual(wb["用户消费"]["D4"].value, "=SUM(D3:D3)")
        summary = wb["区间汇总"]
        self.assertEqual(summary["B4"].value, "='用户模型用量'!E5")
        self.assertEqual(summary["B9"].value, "='用户模型用量'!C5")
        self.assertEqual(summary["B11"].value, "=B4/B10")
        self.assertEqual(summary["B12"].value, "=B9/B10")

    def test_empty_excel_no_circular_formula(self):
        ws = load_workbook(export_excel([], "2026-07-26", "2026-08-25")).active
        self.assertEqual(ws["E3"].value, 0)
        self.assertEqual(ws["O3"].value, 0)

    def test_developer_mode_does_not_change_excel(self):
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch("new_api_statistics.app.load_report", return_value=self.rows),
        ):
            books = []
            for suffix in ["", "&dev=0", "&dev=1"]:
                response = app.test_client().get(
                    "/statistics/api/export?start=2026-07-26&end=2026-08-25" + suffix,
                    auth=("test_admin", "testing"),
                )
                self.assertEqual(response.status_code, 200)
                wb = load_workbook(BytesIO(response.data))
                self.assertEqual(wb.active.max_column, 15)
                books.append([[list(row) for row in ws.values] for ws in wb.worksheets])
            self.assertEqual(books[0], books[1])
            self.assertEqual(books[0], books[2])

    def test_auth_and_user_model_filters(self):
        with patch(
            "new_api_statistics.app.verify_admin",
            side_effect=lambda u, p: u == "test_admin" and p == "testing",
        ):
            client = app.test_client()
            self.assertEqual(client.get("/statistics/api/usage").status_code, 401)
            with patch(
                "new_api_statistics.app.load_report", return_value=deepcopy(self.rows)
            ):
                response = client.get(
                    "/statistics/api/usage?start=2026-07-26&end=2026-08-25",
                    auth=("test_admin", "testing"),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["totals"]["amount"], "11")
                self.assertEqual(response.json["totals"]["request_count"], 6)
                response = client.get(
                    "/statistics/api/usage?user=absent&start=2026-07-26&end=2026-08-25",
                    auth=("test_admin", "testing"),
                )
                self.assertEqual(response.json["rows"], [])
                self.assertEqual(response.json["totals"]["rpm"], "0")
                response = client.get(
                    "/statistics/api/usage?model=gpt-b&start=2026-07-26&end=2026-08-25",
                    auth=("test_admin", "testing"),
                )
                self.assertEqual(len(response.json["rows"]), 1)
                self.assertEqual(response.json["rows"][0]["model_name"], "gpt-b")
                self.assertEqual(response.json["totals"]["amount"], "1")
                response = client.get(
                    "/statistics/api/export?user=%3Ddanger&model=claude-a&start=2026-07-26&end=2026-08-25",
                    auth=("test_admin", "testing"),
                )
                workbook = load_workbook(BytesIO(response.data))
                self.assertEqual(workbook.active["D3"].value, "claude-a")
                self.assertEqual(workbook.active.max_row, 4)
            self.assertEqual(
                client.get("/statistics/", auth=("admin", "testing")).status_code, 401
            )

    def test_token_detail_endpoint_is_separate_from_excel(self):
        token_rows = [dict(self.rows[0], token_id=7, token_name="key-a")]
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch(
                "new_api_statistics.app.load_report", return_value=token_rows
            ) as load,
        ):
            response = app.test_client().get(
                "/statistics/api/usage/by-token?start=2026-07-26&end=2026-08-25",
                auth=("test_admin", "testing"),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["rows"][0]["token_name"], "key-a")
            load.assert_called_once_with("2026-07-26", "2026-08-25", by_token=True)

    def test_token_group_options_and_filtered_summary_endpoints(self):
        token_rows = [dict(token_id=7, token_name="key-a")]
        with (
            patch("new_api_statistics.app.verify_admin", return_value=True),
            patch(
                "new_api_statistics.app.load_token_options", return_value=token_rows
            ) as options,
            patch(
                "new_api_statistics.app.load_group_options",
                return_value=[{"group_name": "auto"}],
            ) as groups,
            patch("new_api_statistics.app.load_report", return_value=self.rows) as load,
        ):
            client = app.test_client()
            response = client.get(
                "/statistics/api/usage/tokens?start=2026-07-26&end=2026-08-25",
                auth=("test_admin", "testing"),
            )
            self.assertEqual(response.json["rows"], token_rows)
            options.assert_called_once_with("2026-07-26", "2026-08-25")
            response = client.get(
                "/statistics/api/usage/groups?start=2026-07-26&end=2026-08-25",
                auth=("test_admin", "testing"),
            )
            self.assertEqual(response.json["rows"], [{"group_name": "auto"}])
            groups.assert_called_once_with("2026-07-26", "2026-08-25")
            response = client.get(
                "/statistics/api/usage/by-selection?start=2026-07-26&end=2026-08-25&token_id=9&token_id=7&group=auto&by_token=1",
                auth=("test_admin", "testing"),
            )
            self.assertEqual(response.status_code, 200)
            load.assert_called_once_with(
                "2026-07-26",
                "2026-08-25",
                by_token=True,
                token_ids=[7, 9],
                groups=["auto"],
            )
            response = client.get(
                "/statistics/api/usage/by-selection?start=2026-07-26&end=2026-08-25&token_id=bad",
                auth=("test_admin", "testing"),
            )
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
