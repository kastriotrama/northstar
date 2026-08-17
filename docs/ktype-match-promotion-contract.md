# KType match promotion and controlled alias attachment

SCRUM-170 consumes only the current `resolved` decision heads persisted by
SCRUM-171. `provisional` and `review_required` decisions are excluded at the
PostgreSQL query boundary.

The graph writer supports three explicit modes:

- `dry_run` validates every TecDoc KType target and alias conflict gate without
  changing Neo4j;
- `controlled` applies at most the configured cohort limit (1,000 by default);
- `production` applies a preflighted batch without that cohort limit.

Before a write, every selected KType must resolve through exactly one TecDoc
`k_type` alias. A Transportstyrelsen alias may have no existing target or may
already target the same vehicle; a conflicting target stops the whole batch.
One input alias selecting multiple KTypes is rejected before Neo4j is called.

The write removes `Provisional` from the selected KType and attaches a
collision-safe Alias assertion carrying the immutable match decision ID, source
version and confidence. Repeated execution is idempotent. Reconciliation reports
only decision assertions that are missing, multiply targeted, or refer to a
different decision ID.
