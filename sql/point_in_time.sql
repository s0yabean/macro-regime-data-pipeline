-- Point-in-time ("as of") snapshot: the latest value of every series that a
-- strategy could have known on :as_of. This is the only query shape the signal
-- clock should use.
--
-- Run:
--   sqlite3 -header -column data/macro.db \
--     "$(sed 's/:as_of/'"'"'2025-10-10'"'"'/g' sql/point_in_time.sql)"
-- or use pipeline/query_as_of.py, which binds :as_of safely.

SELECT
  ro.series_id,
  sc.display_name,
  sc.native_frequency,
  sc.coverage_status,
  ro.observation_date,
  ro.available_at,
  ro.value
FROM raw_observations ro
JOIN series_catalog sc ON sc.series_id = ro.series_id
WHERE ro.available_at <= :as_of
  AND ro.observation_date = (
    SELECT MAX(r2.observation_date)
    FROM raw_observations r2
    WHERE r2.series_id = ro.series_id
      AND r2.available_at <= :as_of
  )
ORDER BY ro.series_id;
