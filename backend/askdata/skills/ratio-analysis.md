# ratio-analysis

## Description
Compute the ratio between two groups or categories. Return the count/value for each group and the ratio.

## When to Use
- What is the ratio of X to Y?
- What percentage of total does X represent?
- How many X per Y?

## SQL Pattern
```sql
SELECT
  CAST(SUM(CASE WHEN <condition_for_group_a> THEN <value_or_1> ELSE 0 END) AS REAL)
    / NULLIF(SUM(CASE WHEN <condition_for_group_b> THEN <value_or_1> ELSE 0 END), 0) AS ratio,
  SUM(CASE WHEN <condition_for_group_a> THEN <value_or_1> ELSE 0 END) AS group_a_count,
  SUM(CASE WHEN <condition_for_group_b> THEN <value_or_1> ELSE 0 END) AS group_b_count
FROM <table>
```
