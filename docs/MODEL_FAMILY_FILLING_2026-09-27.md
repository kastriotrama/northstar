# Model family for cars the registry named only by make (2026-09-27)

| | |
|---|---|
| Status | Done locally, not on live |
| Branch | `feature/vehicle-core` (PR #55) |
| Measured on | 30,000-car impact sample and the independent 50,000-car test set (`outputs/sample-db`) |
| Catalog | `tecdoc-0326-canonical-full-prod-v2-20260914` |

## The problem

2,366,597 registered passenger cars (37%) had no model family. Their registry text
names only the make ("VOLVO"), a manufacturer code ("VOLVO 744-883 GL", "BMW 325")
or a model that TS normalization has no rule for. The matcher could read a model
from that text for some of them, but 15.6% of all cars still stopped at
`model_evidence_missing`.

## What changed

**1. Model family rules** (`vehicle_core_rules`, [vehicle-core-design.md](vehicle-core-design.md#learned-rules)).
Seven rule families fill `core.vehicles.model_family`. Six are learned from the TS
vehicles that have a model:

- variant + version;
- VIN characters 1–8;
- type code;
- variant;
- brand text;
- the brand text's model word.

On a 10% holdout of vehicles with a known model they predict it 99.77–99.99%
right. The seventh (`MOD-PAT`) reads old manufacturer codes and model words with
reviewed patterns: Volvo 744 → 740, BMW 325 → 3 Series, Mercedes C 180 → C-Class,
PEUGEOT 307 → 307. Each pattern rule is checked against the known vehicles under
its key.

**2. The model guard** (`vehicle_model_guard`). The holdout only holds vehicles
with a model, so it could not see models TS never named. Bora, Jetta, Sharan,
Sintra, Carens and the Kia Rio share a key with a sibling TS did name, and got the
sibling's model: "VW BORA 1,6" became a Golf, "KIA RIO" a Niro, "NISSAN MICRA" a
Qashqai.

No fill is made, and `check-model-fills --retract` takes back an existing one,
when the car's own model word names another catalog family. The model word is
the registry model field, or the word after the make. It is read with the
matcher's own reader, so the guard and matching agree.

A brand-word rule keyed on a number that is not a model name of the make only
fills cars of the era it was learned from (±2 years). For example, Mercedes-Benz
"220" was learned from W220 S-Classes and would otherwise have made the 1970s
"220 D" saloons S-Classes.

**3. How the matcher reads a model from registry text**
(`fuzzy_matching.recover_model_from_evidence`). There are three readings:

- **`model_word`** (default):
  - **The model word decides.** A catalog model's name read at the model word
    (the registry model field, or the word after the make) outranks every other
    label: "911 CARRERA 4 GTS" is a 911, not the longer "CARRERA GT".
  - **Names, not trims or codes.** A label is a model's name when it starts with
    the model's first word and keeps its other words in order: "GOLF VARIANT"
    names "GOLF VII Variant". A chassis code ("ED") or an engine number ("200") in
    that position gets no priority.
  - **Family words.** In brand text, a word of three or more letters that starts
    catalog names counts as that family (PASSAT → the Passats).
  - **Numbers and short names** are models in the model position (307, 9000, 940,
    7X). Elsewhere they are refused, because a number can be a displacement.
  - **Spelling.** "RAV4" matches TecDoc's "RAV 4".
  - **What is not read.** The make that brand text starts with ("MINI") names no
    model. A trim shared by several models names none of them.
- **`strict`**, against a model a rule inferred: only a model's name counts. A
  number, trim or body word never overrules the rule ("S 600 COUPE" is no 1960s
  COUPE, "300 TD" no 1950s 300). A number in the model position still stops later
  words being read ("911 CARRERA" is no CARRERA GT).
- **`legacy`**: the longest label anywhere. The fail-closed check between the
  model field and brand text still reads this way, so reading model words never
  adds a disagreement that sends a car to review.

**4. Rule-inferred models yield to the car's text.** When a car's model came from
a rule, the matcher reads the car's text strictly first. If that names nothing,
an ordinary reading may still make the rule's model more precise, but only
within the same family ("BMW 630 CS" → 6 (E24) of a "6 Series"). Otherwise the
rule's model is used.

**5. AIS-only cars show their registry text to the matcher.** Their brand and
model text come from the vehicle record, since they have no TS record.

## Results

Share of cars resolved to one KType, on the two fixed car sets:

| | 30,000-car sample | 50,000-car test set |
|---|---|---|
| Before | 49.5% | 49.0% |
| With the model work | 54.5% (+1,516 cars, −10) | 54.0% (+2,522 cars, −16) |
| Final: era check, and the merged matcher change `65f6eb6` | **55.1%** (+1,763, −84) | **54.6%** (+2,923, −121) |

The final row also carries a matcher change merged from the PR (a year one
outside a KType's run is unverified, not a conflict; engine revision letters name
a family). On the 30k, 247 of the 250 cars gained and all 77 cars lost since the
model-work run depend on it. The model work alone is the middle row.

**The check.** 1,933,638 rule-filled models were checked. 6,524 were taken back
as outside their rule's learned era, among them the 1950s Mercedes-Benz 170 S
filled as SLK and pre-war Citroën 7 CV filled as Berlingo. No fill contradicted its
car's text any more. Re-applying the rules afterwards made no new fills.

**Cars that changed KType** (21 on the 30k, 44 on the 50k) were checked one by
one. Each moved to a KType its registry text names more precisely:

- Qashqai+2 (was Qashqai);
- C4 Cactus (was C4);
- 5008 (was 3008);
- V60 Cross Country (was V60);
- i20 Active (was i20);
- X2 (was X1);
- one 2001 Pajero with Pajero II's exact power (was Pajero Sport).

**Cars the model work lost on the 50k** (11 after the era check):

- 5 × Range Rover Evoque. Before, they resolved to the wrong Range Rover IV. Now
  they wait for review with the Evoque on top.
- 5 × "EXPERT TRAVELLER". They were resolved to Traveller Bus, and Expert Bus is
  on the same platform. Review is the safe outcome.
- 1 × BMW "328I XDRIVE". "328I" is an engine number, not a model name, so it no
  longer selects the F30 by itself. It waits for review with the F30 on top.

The era check brought back 4 of the 5 Mercedes 220 D / 220 SE the model-work run
had lost. The fifth resolves again only without the merged year tolerance.

## What is left

- **Cars still without a model.** 721,810 registered passenger cars (11%) have no
  model family, down from 2,366,597. Rules filled 1,644,787. Most of the rest are
  pre-1980 classics and new models TecDoc does not carry yet: no rule can name a
  model the catalog lacks.
- **For the matcher change `65f6eb6`:**
  - The one-year tolerance loses 32 cars on the 30k (Hyundai i20, Peugeot, VW
    and others): the neighbouring generation comes within the margin.
  - The revision families send 45 Subaru Outback 2018–2020 to review. They are
    between Outback BS, which carries FB25B, and an open-ended Outback BR KType
    with no engine. Holding them is probably right for 2018–2019.
  - Resolved cars whose engine differs from the KType's rose from 0.4% to 0.8% on
    the 30k.
- **The 50k sample dump** predates the model rules. It needs rebuilding with the
  rules and fills.
- **Live** takes the rules in the order below, after the catalog batch is loaded.

## Operating it

```bash
python -m ingestion.cli learn-vehicle-rules --family MOD-VV --family MOD-VIN --family MOD-TP --family MOD-VAR --family MOD-BR --family MOD-BT --family MOD-PAT --activate
```

```bash
python -m ingestion.cli apply-vehicle-rules --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 --family MOD-VV --family MOD-VIN --family MOD-TP --family MOD-VAR --family MOD-BR --family MOD-BT --family MOD-PAT
```

```bash
python -m ingestion.cli check-model-fills --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 --retract
```

Run the check after any catalog or matcher change as well.
