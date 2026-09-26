# Cost observation methodology

## Scope

`cost-observation-v1` records timestamped price context for one exact
Controller or Agent target. It is an immutable operator claim, not a provider
price feed, billing reconciliation, price/performance result, or provider
rating input.

CloudMark snapshots the selected target's provider, provider-evidence source
and confidence, product/SKU, region, zone, operating system, architecture, and
hostname at creation time. A target without provider and SKU identity is
rejected rather than creating unbound cost evidence.

## Input contract

The authenticated request must provide:

- an exact positive decimal string with at most 12 integer and six fractional
  digits; JSON floating-point numbers are rejected;
- a three-letter uppercase-normalized currency code;
- one fixed billing unit: hour, month, year, or one-time;
- one commitment class: on-demand, spot, reserved, contract, or unknown;
- explicit tax state: included, excluded, or unknown;
- either a public provider HTTPS URL without embedded credentials/fragment or
  a bounded operator reference; and
- an optional timezone-aware source observation timestamp.

When the request omits `observed_at`, the Controller receipt time is used and
`observed_at_source=controller-receipt-time` is retained. A supplied timestamp
is normalized to UTC and labelled `operator-supplied`. Timestamps more than one
day in the future are refused.

Do not enter passwords, access tokens, signed URLs, customer/account IDs,
unredacted invoice content, or provider credentials. `operator-reference` is
for a bounded redacted identifier only. CloudMark stores no document and never
fetches the supplied source.

## Evidence and interpretation

Every record has:

- `evidence_status=operator-declared-unverified`;
- `provider_rating_input=false`;
- `price_performance_calculated=false`;
- `missing_terms_inferred=false`; and
- the exact target and source provenance described above.

Monthly, hourly, annual, one-time, spot, reserved, contract, taxed, and untaxed
values are not normalized or compared. CloudMark does not assume month length,
usage volume, commitment duration, included transfer/storage, discounts,
credits, licenses, exchange rates, or tax jurisdiction. These terms require a
future independently reviewed cost methodology.

The dashboard displays recent records as operator claims. Recording cost
context does not change the `not-rated` provider status or satisfy the cost
criterion in suitability/provider readiness.

## Persistence and API

Cost observations are stored in the `cost_observations` SQLite table as
immutable versioned JSON and are included in normal runtime snapshots. No
update or delete endpoint exists.

```http
GET  /api/v1/cost-observations
POST /api/v1/cost-observations
```

The POST endpoint requires the Controller token. The GET endpoint returns at
most 200 recent records; the compact five-second dashboard read model includes
at most 20.
