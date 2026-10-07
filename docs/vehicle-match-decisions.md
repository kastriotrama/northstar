# Six matching decisions (proposed 2026-10-07)

Six rulings that let cars resolve which the matcher stopped before. Each is a
choice a stakeholder owns, not a technical invariant. They are **in force in the
code as proposals** (`claude-proposal-2026-10-07`) and await the data owner's
confirmation; each can be switched off on its own.

The stakeholder wording, with one yes/no question per decision, is in the
shared document "Vehicle matching: six decisions needed".

## What each decision does

| # | Decision | What changed | Switch |
|---|---|---|---|
| 1 | A candidate-only KType that is the car's only fit is its match | `TecDocDryRunEvaluator`: a candidate-only KType is accepted when routing would resolve an approved one and no other candidate fits the car. Reason code `candidate_only_sole_fit`. | `accept_sole_candidate_only=False` |
| 2 | A registry "MPV" fits a TecDoc "SUV" | One row in `core.tecdoc_resolution_rules` (`bodywork`, `multi_purpose_vehicle` compatible with `suv`). Broader-than, never a confirmation: an MPV KType of the same model stays ahead. | Set the row's decision to `excluded` |
| 3A | Reviewed pairs of registry power and TecDoc power for electric models | `ingestion/tecdoc/power_equivalences.py`: Audi A6 / Q6 e-tron, Porsche Macan / Taycan, MINI Countryman (U25), Polestar 2. A pair counts as the same power, for a car and a KType that are both electric only. | `FuzzyMatchConfig.reviewed_power_equivalences=False` |
| 3B | An electric car within 2 % of a KType's power | Unverified instead of a conflict, only where that KType's figure is the one figure of its model that near. | `electric_power_tolerance=0.0` |
| 4 | Renault writes the motor family, not a variant | `engine_code_aliases.registry_motor_family`: `5AQ-60` / `5AQ-80` are family evidence on a KType carrying `5AQ 601` / `605` / `607`. Power says which KType. | Remove the Renault pattern |
| 5 | One listed motor on a four-wheel-drive electric KType | `EngineCodeCatalog`: an electric KType with a single code that the car's list includes, both four-wheel drive, same power up to 2 kW, is family evidence instead of a conflict (Kia EV9). | Remove the exception |
| 6 | A veteran car's power contradicts no KType | For a car built before 1975 a differing power is unverified, only while no plausible KType carries the car's figure. | `veteran_power_before_year=0` |

None of these makes an engine code confirm a KType it did not confirm before,
and none changes how an approved KType with an exact engine code resolves.

## Why each is safe enough to propose

1. **Candidate-only.** Checked against cars with a known answer: 20,000 cars
   whose candidate-only KType the car's own engine code confirmed were matched
   again with the engine code hidden. The rule accepted 11,094 of them, all
   11,094 to the known KType. The rest were held back (not the only fit, or a
   routing gate not met).
2. **MPV / SUV.** The registry's class (EU body code AF) is broader than
   TecDoc's. The compatible pair carries the same mild penalty as the earlier
   MPV / van and MPV / bus rulings, so an exact MPV KType of the model still
   wins by more than the automatic margin.
3. **Electric power.** The pairs are the maker's own rated and peak figures and
   line up one to one, in ascending order and by driven axles, within each
   model. Models where they do not (BMW iX1: one registry figure, two KTypes)
   are left out. The 2 % tolerance never applies between two KTypes of a model
   that are both that near (a Tesla Model Y at 255 and 258 kW).
4. **Renault.** `5AQ-60` is registered on Zoes of 65, 68 and 80 kW, whose
   KTypes carry `5AQ 601` and `5AQ 605`, so it cannot be a variant code. Every
   Zoe KType has its own power.
5. **Kia EV9.** TecDoc lists `EM16` alone on its four-wheel-drive EV9 KTypes
   as on the rear-drive ones; the car names both motors. The general rule for
   lists stays strict.
6. **Veterans.** The registry's figure for a 1950s or 60s car is often none of
   TecDoc's (a Volvo Amazon at 55 kW; TecDoc lists 49, 59, 63 and 66). Where a
   plausible KType does carry the car's figure, power decides as before.

## Measured (local copy of the full register, 2026-10-07)

Before / after all six, with the standard impact report:

| Sample | Before | After | Gained | Lost | Moved |
|---|---|---|---|---|---|
| Seeded 30k | 70.8 % | 73.8 % | 908 | 0 | 0 |
| Random 20k | 70.7 % | 73.6 % | 578 | 0 | 0 |

Control groups of cars already matched (electric, registered MPV, built before
1975, several engine codes; 14,000 cars) kept every match.

Then every car the decisions can reach was matched again (1,173,607 cars: every
electric car, every car registered MPV, every car built before 1975, every car
with several engine codes, Renault `5A?-NN` codes, and every car with one
candidate-only KType) and compared car by car with the results before:

| | Before | After |
|---|---|---|
| Resolved, whole register (6,427,730 cars) | 4,551,032 (70.80 %) | 4,742,866 (73.79 %) |
| Resolved, cars AIS added (657,743) | 286,517 (43.56 %) | 362,829 (55.16 %) |
| One KType, not accepted | 254,248 | 112,112 |
| No KType | 402,094 | 311,244 |
| Several KTypes | 1,027,911 | 1,069,063 |

191,834 cars gained a match, none lost one, and 2 moved to another KType (a
Mitsubishi Pajero and an Audi SQ7, both registered MPV, from the model's van
KType to its SUV KType).

| # | Decision | Gained a match | Other effect |
|---|---|---|---|
| 1 | The only KType that fits | 146,822 | 161,464 matched cars carry `candidate_only_sole_fit` in all (decisions 2 to 4 release cars onto candidate-only KTypes too) |
| 2 | Registry MPV, TecDoc SUV | 13,184, and most of 7,137 electric cars registered MPV (BYD Atto 3) | 1,723 from no KType to several |
| 3 | Electric power | 8,671 | 521 from no KType to several |
| 4 | Renault motor code | 8,825 | |
| 5 | One listed motor (Kia EV9) | 0 | 6,579 from no KType to several: TecDoc has two four-wheel-drive EV9 KTypes of the same power |
| 6 | Veteran power | 7,195 | 32,147 from no KType to several, for a person to choose |

What two earlier versions of decision 6 cost, and why it reads as it does: with
the plain tolerance penalty, 38 of 50,000 sample cars lost their match (every
one a veteran that had resolved on its power and now tied with a sibling);
with a heavier penalty, 10. Judged among the plausible KTypes only -- power
says nothing only while none of them carries the car's figure -- none is lost.

## Applying it to a database

1. Deploy the code.
2. Add the body ruling (decision 2) through the rules bundle or the TecDoc
   review screen.
3. Re-match the cars the decisions can reach instead of rebuilding everything:
   electric cars, cars registered MPV, cars built before 1975, cars with several
   engine codes, Renault cars with a `5A?-NN` code, and cars with one
   candidate-only KType. Write their NOR IDs to a file and run
   `python -m scripts.refresh_vehicle_match_results --catalog-batch <batch>
   --vehicles-from <file> --force --workers 4`.

## Taking a decision back

Switch it off as the table says, then re-match the same cars.

## Checking the matches of decision 1

Matches made by decision 1 carry the reason `candidate_only_sole_fit`. In
Vehicles > Cars, open "What stands between the open cars and one KType": the
section "Matched — to check" counts them under the current filter, and the
count narrows the list to exactly those cars. The overview endpoint returns the
same number as `resolved_only_fit`.
