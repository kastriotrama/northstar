# TS→TecDoc reviewed Peugeot hard-conflict repair

Date: 2026-08-31

The mixed-fuel v6 development control exposed four Peugeot 3008 rows whose
leading hypothesis changed from the 3008 II HNS family to a 3008 III HPY hybrid.
That produced a new power conflict even though TS consistently reports petrol,
1199 cc, 96 kW and version `HNSU-C16E00`.

Two exact source signatures were reviewed and approved. They differ only by
the full type-approval extension (`*25` or `*26`) and both require source model
3008, type `M`, variant `R` and version `HNSU-C16E00`.

The repair supplies only the canonical TecDoc family query
`3008 II SUV (MC_, MR_, MJ_, M4_)`. It does not select a KType, infer an engine,
add score, remove bodywork evidence or bypass confidence routing. Candidate
KTypes inside that family must still pass all ordinary technical and margin
gates. The two approval extensions remain separate exact rules; no prefix or
future-extension matching is allowed.

The policy is approved only as a frozen-holdout candidate. It remains inactive
for production, decision persistence, alias attachment and Neo4j promotion
until the final pinned controls and untouched holdout pass.
