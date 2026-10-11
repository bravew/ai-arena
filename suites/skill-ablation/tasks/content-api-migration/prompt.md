Write a concise migration guide for an API endpoint change using only these facts. Include a checklist and a short before/after example; label anything that cannot be determined as a question for the API owner instead of guessing.

Facts:
- `GET /v1/users/{id}/events` is replaced by `GET /v2/users/{id}/events`.
- The response field `timestamp` is renamed to `occurred_at` and remains an ISO 8601 UTC string.
- Pagination now uses `next_cursor`; `page` and `per_page` query parameters are no longer accepted.
- The maximum page size is 100.
- The v1 endpoint will remain available for 90 days after v2 launches.
- No authentication or rate-limit changes are specified.
