# compare-periods

## Description
Compare a metric between two time periods. Return both values and the absolute or percentage change.

## When to Use
- How did X change between period A and period B?
- Compare revenue last month vs the month before.
- Year-over-year growth of X.

## SQL Pattern
```sql
SELECT
  SUM(CASE WHEN <time_column> BETWEEN '<period_a_start>' AND '<period_a_end>' THEN <metric_column> ELSE 0 END) AS period_a_value,
  SUM(CASE WHEN <time_column> BETWEEN '<period_b_start>' AND '<period_b_end>' THEN <metric_column> ELSE 0 END) AS period_b_value,
  SUM(CASE WHEN <time_column> BETWEEN '<period_a_start>' AND '<period_a_end>' THEN <metric_column> ELSE 0 END)
    - SUM(CASE WHEN <time_column> BETWEEN '<period_b_start>' AND '<period_b_end>' THEN <metric_column> ELSE 0 END) AS absolute_change,
  ROUND(
    (CAST(SUM(CASE WHEN <time_column> BETWEEN '<period_a_start>' AND '<period_a_end>' THEN <metric_column> ELSE 0 END) AS REAL)
     - SUM(CASE WHEN <time_column> BETWEEN '<period_b_start>' AND '<period_b_end>' THEN <metric_column> ELSE 0 END))
    * 100.0 / NULLIF(SUM(CASE WHEN <time_column> BETWEEN '<period_b_start>' AND '<period_b_end>' THEN <metric_column> ELSE 0 END), 0),
  2) AS pct_change
FROM <table>
```
