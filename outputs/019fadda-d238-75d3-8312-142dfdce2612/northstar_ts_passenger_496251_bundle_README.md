# NorthStar 496,251-passenger-vehicle import bundle

This bundle contains the complete local Transportstyrelsen passenger-car batch,
split into 100 importable Excel workbooks:

- `northstar_ts_passenger_496251_part_001_of_100_2026-08-10.xlsx` through
  `northstar_ts_passenger_496251_part_100_of_100_2026-08-10.xlsx`
- Parts 001–099 contain 5,000 vehicles each.
- Part 100 contains 1,251 vehicles.
- Total: 496,251 unique staged source records and 496,251 expected normalized results.
- Only Transportstyrelsen passenger vehicles from the approved local batch are included.

## Matching rules

- Application catalog: `ts-translation-v7`
- Immutable active rule version: `ts-review-20260810T143500000000Z`
- Pipeline: `normalization-pipeline-v5`
- The latest `TS-SPECIAL-VEHICLE-V1` policy is embedded in every workbook.
- The same immutable policy can be installed with
  `northstar_special_vehicle_policy_v1.sql`.

## Expected totals

| Status | Vehicles |
|---|---:|
| Resolved | 269,803 |
| Provisional | 223,367 |
| Review required | 3,081 |
| Failed | 0 |
| **Total** | **496,251** |

The refreshed result set includes 2,940 vehicles carrying official
special-purpose body codes and 94 passenger records whose source text identifies
them as `AMATÖR`. The policy keeps the raw TS evidence, marks amateur-built
vehicles as `Special Modified`, excludes those vehicles from automatic
TecDoc/parts matching, and routes other special-purpose vehicles through the
appropriate safety policy.

## Import order

1. Check out the matching PR/application version.
2. Use an isolated test database.
3. Apply `northstar_special_vehicle_policy_v1.sql` if the target immutable rule
   version is not already installed.
4. Import parts 001–100 in numeric order with the normalization-bundle importer.
5. Confirm the final total and status counts above.

Each workbook includes source rows, expected normalized results, translation
rules, base and effective manufacturer entities, active overrides, immutable rule
metadata, and known issues. The full original source and normalized payloads are
stored losslessly in JSON columns.
