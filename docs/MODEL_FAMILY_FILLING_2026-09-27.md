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

**Added 2026-10-01 (local, not on live): the car's own text in TS's words, and reviewed eras.**

- When the model field names nothing the catalog knows, the guard reads the brand
  text's model word: model "SL" with brand "KIA SPORTAGE 2,0 CRDI EX" is no Sorento.
  The matcher itself does not fall back this way.
- The model field and the brand text are also read the way `MOD-MT`/`MOD-BRT` read
  them (`model_text_family`, TS vocabulary plus the reviewed tables), with the same
  sibling check: when the TS-named cars with the very same text mostly carry another
  family, the reading counts for nothing. TS states Clio for all 60 cars with the
  brand text "RENAULT B", so "B" is not read there.
- A fill is refused when a text names a family and no text names the fill's family:
  "MAZDA2" is no CX-3, "MAZDA6" no CX-5, VW "CC" no Passat, "FIAT DOBL 1,6" no 500,
  "VOLVO P 44507 M" (a 1950s Duett) no 440.
- When the model field names the fill in TS's words, no catalog reading refuses it.
  Brand "JAGUAR XK8" with model "XKR CONVERTIBLE" is an XKR, although TecDoc files
  the XKR under the XK 8, and "PLYMOUTH BELVEDERE" with model "GTX/SPORT" is a GTX.
- A model field that is exactly one family's name ("EX30") refuses a narrower
  sibling ("EX30 Cross Country") unless another text names it.
- **A unanimous VIN-descriptor rule outranks one car's text in TS's words.** This
  applies to `MOD-VIN`, `MOD-VINL` and `MOD-VINY` rules where every TS-named car
  under the key agreed, for a car built within the years of those cars (±2). Such a
  rule is not refused by a reading of a family TS states elsewhere, and it lets a
  narrower sibling stand.
  - Kept: "LEXUS LS250" (446 TS-named IS under its descriptor), "MAZDA MZ-5", "MG F"
    on a 100 kW TF, a "GRANTURISMO SPORT" convertible under the GranCabrio's
    descriptor, "BMW 120I GRAN TOURER", "MERCEDES-BENZ C LK320" and "E350CLS", and
    the 4 EX30 Cross Countrys registered "EX30".
  - Not kept: a reviewed name TS never states ("CC", "Caravelle") says nothing by its
    absence under the key, so VW "CC" is still no Passat. A key a maker reused
    decades later ("BMW 735 IA" of 1984 under the X6's descriptor) is outside its
    years, so the reading still refuses the fill.
- **Reading fixes**, shared with the learners (`vehicle_model_patterns`):
  - A make word written over several registry words must end where one of them
    ends, so "FORD-T" is no make in "FORD T.E.C" (no Ford "E"). Within the first
    registry word it may end anywhere ("VW-GOLF", "CHEV.ASTRO"). A learned make word
    that is the make glued to a number ("BMW525") is never one, so "BMW 525 IX" is a
    5 Series, not the iX.
  - "MERCEDES B" and "M B" spell Mercedes-Benz: "MERCEDES B E240" is an E-Class.
  - A number with a decimal point or comma is an engine size, letters glued to it
    included ("4,2Q", "3,0I"): "MAZDA 2.3 KOMBI" is no Mazda 2, and "MAZDA 929 3,0I"
    no Mazda 3. A number that spells a name still names it: "SAAB 9.3" is a 9-3.
  - A make word that is also a family, written first ("CHEVY CAPRICE"), is the make.
  - A Volvo text naming two sibling series ("944-964", "745-765") names neither, and
    the engine decides between 940 and 960: B6xxx is a 960, four-cylinder B2xx a 940,
    and a D24 diesel decides nothing. A number after a type code is its engine or
    version, even when it looks like a type code ("944-855" is a 940).
  - BMW numbers with a 0 as second digit ("306 D5", the 535d's engine; the 1950s
    507) name no series.
  - "MZ-5" and "5X-5" are typing errors for the MX-5.
- **Reviewed tables:**
  - Renault "B" is not a family (`REVIEWED_NON_FAMILIES`).
  - The Renault 4CV ("4 CV", "R 1062") has no family (`REVIEWED_OTHER_CARS`).
  - iX1/X1 and iX3/X3 are one family (`REVIEWED_SAME_FAMILIES`). TS files every
    iX1 and iX3 it names under X1 and X3.
  - Some naming questions are on hold with the data owner (`NAMING_ON_HOLD`). Until
    they are decided, these fills are not refused:
    - Multivan fills on T4/T5 cars whose text says CARAVELLE;
    - 4x4 fills where the text says Niva;
    - Megane fills where the text says Scenic.
- A motorhome converter in the text (`REVIEWED_CONVERTER_WORDS`: Rapido, Hymer,
  Adria...) keeps only the van families it builds on (`REVIEWED_CONVERTER_BASES`).
- **Reviewed eras** keep a rule from filling a car of another era. There is still no
  generic TecDoc-year check.
  - Per rule (`REVIEWED_RULE_ERAS`), for keys that meant another car in another
    era:
    - "MERCEDES-BENZ 230" and "MERCEDES BENZ 230" → SL only from 2001 (1963-67
      fills are mostly W110/W111 saloons, so the few W113 230 SLs stay unfilled);
    - "S4" → A4 from 1995;
    - Citroën "B" → C4 from 2004.
  - Per family (`REVIEWED_FAMILY_ERAS`), whatever rule or key answers with it, and
    after any re-learn:
    - Saab "93" only 1955-60;
    - Renault "4" from 1961, Clio from 1990;
    - Mercedes A-Class from 1997, B-Class from 2005;
    - Audi Cabriolet 1991-2000, Coupe 1980-96, Fiat Coupe 1993-2001;
    - Ford Galaxie 1958-74 (model years 1959-74).

    A text read as one of these families on a car built outside its years names no
    family. "AUDI S5 COUPE", "S4 CABRIOLET" and "TTS COUPÉ" are A5, A4 and TT, and the
    "AUDI CABRIO 2,4" of 2002 is an A4 Cabriolet, while the "AUDI S2 COUPE" of 1993
    is still the Coupe. TS's own cars confirm it too: TS states A5 for all 14 cars
    with the brand text "AUDI S5 COUPE 4,2Q".
- **Dry run (2026-10-01, read-only, local copy of live).** `check-model-fills` with
  this guard would take back 20,845 of 2,477,659 rule fills. The largest groups:
  - 13,929 EX30 → EX30 Cross Country (type code "2");
  - 2,727 "MAZDA2" → CX-3;
  - 1,113 "CC" → Passat;
  - 801 "MERCEDES(-)BENZ 230" → SL outside its eras;
  - 710 Kia "SL" → Sorento.

  A refill simulation (learn, then apply) gives 19,278 of these cars a family again,
  such as EX30, 2, CC, Sportage and Duett, and leaves 1,567 empty. The match impact
  report has not been run on this.
- **The next learn retires some pattern rules.** `--activate` retires them in status
  only, and their fills stay until `retire_rule` takes them back.
  - Worth retiring with `retire_rule`, because their fills are wrong:
    - `MOD-PAT` BMW numbers with a 0 second, mostly the 1950s "501", "502", "503",
      "507", "600" and "700" filled 5/6/7 Series (14 rules, 190 fills), and the
      matching `MOD-BRT` brand texts (27 fills);
    - `MOD-BRT` "MAZDA 929 3,0I" → 3 (33 fills);
    - the "FORD T.E.C…" texts → E (16 fills);
    - "RENAULT B/C 53" → B.
  - Not to be retired: the Volvo two-series `MOD-PAT` rules ("944-964", "945-965",
    "744-764", "745-765", "744-762", "745-762"; 122 fills). Their four-cylinder cars
    are right, and the engine check already takes back the six-cylinder ones.

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

The manufacturer first: the model families are learned per manufacturer, so cars
whose make only the brand text names must have it before they are learned.

```bash
python -m ingestion.cli learn-vehicle-rules --family MFR-BW --activate
```

```bash
python -m ingestion.cli apply-vehicle-rules --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 --family MFR-BW
```

```bash
python -m ingestion.cli learn-vehicle-rules --family MOD-VV --family MOD-VIN --family MOD-VINL --family MOD-VINY --family MOD-TP --family MOD-VAR --family MOD-BR --family MOD-BT --family MOD-MT --family MOD-BRT --family MOD-PAT --activate
```

```bash
python -m ingestion.cli apply-vehicle-rules --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 --family MOD-VV --family MOD-VIN --family MOD-VINL --family MOD-VINY --family MOD-TP --family MOD-VAR --family MOD-BR --family MOD-BT --family MOD-MT --family MOD-BRT --family MOD-PAT
```

```bash
python -m ingestion.cli check-model-fills --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 --retract
```

Run the check after any catalog or matcher change as well. A rule whose answer
changes on a later learn is retired only in status by `--activate`; retire it with
`retire_rule` first (it takes back what it filled), then apply again.
