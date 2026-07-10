# rank-top-bottom

## Description
Rank items by a metric and select the top or bottom N results. Always use ORDER BY with LIMIT.

## When to Use
- Which X has the most or least Y?
- Top N by Z.
- Bottom N with lowest W.

## SQL Pattern
```sql
SELECT <columns>
FROM <table>
WHERE <filters>
ORDER BY <metric_column> DESC
LIMIT <N>
```
