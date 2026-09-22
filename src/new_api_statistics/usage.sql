-- Aggregate in PostgreSQL. Parameters use an inclusive start, exclusive end.
WITH source AS MATERIALIZED (
    SELECT id, created_at, user_id, username,
           token_id AS source_token_id, token_name AS source_token_name,
           model_name, quota::numeric,
           COALESCE(prompt_tokens, 0)::bigint AS p,
           COALESCE(completion_tokens, 0)::bigint AS c,
           COALESCE(NULLIF(btrim(other), ''), '{}')::jsonb AS o
    FROM logs
    WHERE type = 2 AND created_at >= %(start)s AND created_at < %(end)s
      /* scope_channels */
      AND (%(token_ids)s::bigint[] IS NULL OR token_id = ANY(%(token_ids)s::bigint[]))
      AND (%(groups)s::text[] IS NULL OR COALESCE("group", '') = ANY(%(groups)s::text[]))
), parts AS MATERIALIZED (
    SELECT *,
        COALESCE(NULLIF(o->>'cache_tokens', '')::bigint, 0) AS cr,
        COALESCE(NULLIF(o->>'cache_write_tokens', '')::bigint,
                 NULLIF(o->>'cache_creation_tokens', '')::bigint, 0) AS cw,
        COALESCE(NULLIF(o->>'group_ratio', '')::numeric, 1) AS group_ratio
    FROM source
), semantic_parts AS MATERIALIZED (
    -- Application model convention: only Claude excludes caches.
    SELECT *,
        left(lower(model_name), 6) = 'claude' AS separate_cache
    FROM parts
), normalized AS MATERIALIZED (
    SELECT *, CASE WHEN separate_cache THEN p ELSE GREATEST(p - cr - cw, 0) END AS noncache_input
    FROM semantic_parts
), dimensioned AS MATERIALIZED (
    SELECT *,
        CASE WHEN %(by_token)s THEN source_token_id ELSE 0 END AS token_id,
        CASE WHEN %(by_token)s
             THEN COALESCE(NULLIF(btrim(source_token_name), ''), '未知令牌') ELSE '' END AS token_name
    FROM normalized
), totals AS (
    SELECT user_id, username, token_id, token_name, model_name,
        COUNT(*) AS request_count,
        COUNT(DISTINCT group_ratio) AS ratio_count,
        SUM(p) AS raw_input_tokens,
        SUM(noncache_input) AS input_tokens,
        SUM(c) AS output_tokens,
        SUM(cw) AS cache_write_tokens,
        SUM(cr) AS cache_read_tokens,
        SUM(noncache_input) AS pricing_input_tokens,
        SUM(CASE WHEN separate_cache
                 THEN p + c + cr + cw ELSE p + c END) AS total_tokens,
        SUM(quota) / 500000 AS amount
    FROM dimensioned
    GROUP BY user_id, username, token_id, token_name, model_name
), latest AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY user_id, username, token_id, token_name, model_name
        ORDER BY created_at DESC, id DESC
    ) AS rank FROM dimensioned
)
SELECT t.*, m.group_ratio
FROM totals t JOIN latest m
    USING (user_id, username, token_id, token_name, model_name)
WHERE m.rank = 1
ORDER BY t.username, t.user_id, t.token_name, t.token_id, t.total_tokens DESC, t.model_name
