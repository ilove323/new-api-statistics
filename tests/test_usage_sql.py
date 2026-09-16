"""Run with PGHOST configured: python -m unittest discover -s tests.

Read-only PostgreSQL integration test; a CTE shadows logs with synthetic rows.
"""

import json
import os
import unittest
import psycopg
from psycopg.rows import dict_row
from new_api_statistics.report import SQL


@unittest.skipUnless(os.environ.get("PGHOST"), "PostgreSQL integration requires PGHOST")
class UsageSQLTest(unittest.TestCase):
    def test_site_model_convention_and_latest_ratio(self):
        cases = [
            ("gpt", None, 100, 20, 30, 10, 60, 120),
            ("claude-native", "anthropic", 100, 20, 30, 10, 100, 160),
            ("claude-converted", "openai", 100, 20, 30, 10, 100, 160),
            ("alias", "anthropic", 100, 20, 30, 10, 60, 120),
            ("glm", None, 100, 20, 30, 10, 60, 120),
            ("deepseek", None, 100, 20, 30, 10, 60, 120),
            ("kimi", None, 100, 20, 30, 10, 60, 120),
            ("gpt-overlap", "openai", 100, 20, 80, 40, 0, 120),
        ]
        fixtures = []
        for model, semantic, p, c, cr, cw, _, _ in cases:
            for index, ratio in enumerate((3, 3, 3.4), 1):
                other = dict(cache_tokens=cr, cache_write_tokens=cw, group_ratio=ratio)
                if semantic:
                    other["usage_semantic"] = semantic
                fixtures.append(
                    dict(
                        id=index,
                        created_at=index,
                        user_id=1,
                        username="fixture",
                        model_name=model,
                        quota=100,
                        prompt_tokens=p,
                        completion_tokens=c,
                        other=json.dumps(other),
                        type=2,
                    )
                )
        prefix = """WITH logs AS (
            SELECT * FROM jsonb_to_recordset(%(fixtures)s::jsonb) AS r(
                id bigint, created_at bigint, user_id bigint, username text, model_name text,
                quota bigint, prompt_tokens bigint, completion_tokens bigint, other text, type int)
        ), source AS MATERIALIZED ("""
        query = SQL.replace("WITH source AS MATERIALIZED (", prefix, 1)
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        ) as conn:
            rows = conn.execute(
                query, dict(fixtures=json.dumps(fixtures), start=0, end=10)
            ).fetchall()
        by_model = {row["model_name"]: row for row in rows}
        for model, _, p, c, cr, cw, clean, total in cases:
            row = by_model[model]
            self.assertEqual(row["input_tokens"], clean * 3)
            self.assertEqual(row["pricing_input_tokens"], clean * 3)
            self.assertEqual(row["raw_input_tokens"], p * 3)
            self.assertEqual(row["output_tokens"], c * 3)
            self.assertEqual(row["total_tokens"], total * 3)
            self.assertEqual(float(row["group_ratio"]), 3.4)
            self.assertEqual(row["ratio_count"], 2)


if __name__ == "__main__":
    unittest.main()
