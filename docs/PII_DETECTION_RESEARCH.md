# PII detection: what exists, what it actually scores, and what to build

Research prompted by the owner's judgement that the current engine is not the
industry-standard way to do this. It is not, and the research below says what is
— but it also found that the standard tools do not solve our problem shape, and
that their published accuracy does not survive independent measurement.

Written by Claude while Codex was out of quota. Codex implements.

---

## The finding that reframes the problem

**Every serious open-source PII detector does span-level NER over free text.
None of them classify a column.**

Our question is not "which characters in this sentence are a name". It is "given
the column `basvuru_sahibi`, its semantic type, its value shapes and its
uniqueness, is this column personal data". That is a different input and a
different unit of decision.

This matters because it means there is nothing to simply install. Any of these
tools has to be *bridged* to our problem — by running the detector across a
sample of the column's values and aggregating what it finds. That bridge is ours
to write regardless of which detector we pick, and the aggregation rule is where
the accuracy of the whole thing will actually be decided.

It also means the column *name* — the strongest human signal, and the one the
current engine leans on entirely — is not covered by any of these models at all.

## What exists

| Tool | Licence | Size | Turkish | What it is |
|---|---|---|---|---|
| [Presidio](https://github.com/microsoft/presidio) | MIT | framework | via spaCy | Recognizer framework: regex + checksum + NER, bring your own NLP engine |
| [Piiranha-v1](https://huggingface.co/iiiorg/piiranha-v1-detect-personal-information) | MIT | 280M | **no** | DeBERTa-v3 token classifier, 17 PII types, 6 languages |
| [turkish-pii-detector](https://huggingface.co/tugrulkaya/turkish-pii-detector) | OpenRAIL | BERT-base | yes | Exactly our 7 entity types, Turkish-native |
| [tr_core_news_lg/trf](https://huggingface.co/turkish-nlp-suite/tr_core_news_lg) | — | spaCy pipeline | yes | Turkish spaCy models incl. NER — what Presidio would need |

Piiranha covers English, Spanish, French, German, Italian and Dutch. **Turkish is
not among them**, which rules it out as the primary detector for this deployment
however good its numbers look.

## The accuracy problem, which is the same problem the owner raised about us

Piiranha's model card reports **98.27% token detection and 99.44% overall
accuracy**. An independent evaluation —
[*Unmasking the Reality of PII Masking Models*](https://arxiv.org/abs/2504.12308),
17,000 semi-synthetic sentences spanning Indian, UK and US PII formats — measured
the same class of tools and found **the best system, Presidio, at F1 = 0.1385,
with zero recall on most entity types**.

That is not a small discrepancy. It is the difference between a model that works
and one that does not, and the gap exists because the published figure is
measured on the distribution the model was trained on. The paper's own conclusion
is a call for accountability in model cards.

This is precisely the criticism the owner made of our own last round: a test
written to validate yourself will validate you. It applies to the vendors as much
as to us, and it means **no published PII accuracy figure should be taken at face
value, including any we produce.**

The Turkish model is worse on this axis, not better: `turkish-pii-detector`
publishes **no metrics at all**, and its own card says it is not fully reliable
and needs human review.

## Benchmarks

[ai4privacy/pii-masking-openpii-1m](https://huggingface.co/datasets/ai4privacy/pii-masking-openpii-1m)
is the credible public benchmark: CC-BY-4.0, 1.43M rows, 19 entity types,
23 languages. It is what the field measures against.

**Turkish is not in it.** So we can benchmark the non-Turkish half of our
behaviour against something real and independent, and for Turkish we cannot —
which has to be said rather than papered over with another fixture of our own.
Building a Turkish evaluation set is real work with a real cost, and it should be
decided deliberately, not smuggled in as a handful of test cases.

## Recommendation: three layers, and only one of them is new

**1. Keep the checksums. They are the strongest thing we have.**

`_tckn_valid`, `_iban_valid` and `_luhn_valid` are arithmetic, not pattern
matching. A random eleven-digit number does not pass the TCKN check digit; a
random sixteen-digit number does not pass Luhn. They are language-independent,
they fire regardless of what the column is called, and their false-positive rate
is bounded by the checksum itself rather than by a threshold somebody chose. No
NER model improves on them for the formats they cover. They should stay
authoritative: a column of valid TCKNs is PII whether or not a model agrees.

**2. Replace the phrase list with a real detector over the *values*.**

This is where the current engine is weakest and where the off-the-shelf tools
genuinely help. Run the detector across the sampled values and aggregate; a
column whose values are overwhelmingly tagged PERSON is a person column, whatever
its header says. That covers the `hasta_no` case — a valid national ID under an
innocuous column name — which a phrase list misses by construction.

Presidio is the better base than a single model: MIT, it already composes regex,
checksum and NER recognizers, our existing checksums drop in as custom
recognizers rather than being thrown away, and the Turkish NLP engine is
swappable. The Turkish-native model can sit behind that interface as one
recognizer, which keeps its use-restricted licence and unmeasured quality
contained rather than load-bearing.

**3. Use the agent for the column *name*, because nothing else can.**

No available model reads `basvuru_sahibi` and concludes "applicant, therefore a
person". That is semantics, it is exactly what a local LLM is good at, and it is
the half of the problem the ML tooling does not address.

The owner is right that this is now cheap for us: the models are local, the agent
already receives the DataCard, and the DataCard is precisely the evidence a human
would use — name, semantic type, value shapes, uniqueness. Keep it inside the
existing contract: **the agent proposes, the deterministic layer decides.** A
proposal is accepted where value evidence supports it and rejected where a
checksum contradicts it.

### The circularity, named rather than left implicit

If the agent decides what counts as PII, and PII decides what the agent may see,
the agent is setting its own constraint. Two things make this tolerable here and
both should be written down rather than assumed:

- Locally, the egress motivation is close to void anyway — see below.
- The classification the agent influences should be the *feature-exclusion*
  decision, not any future egress control. Those are different jobs and only the
  second is self-referential.

## The thing found while checking the owner's framing

The owner asked me to confirm that the classifier exists so PII does not reach
the agent. It was built for that. **It has never done it.**

`pii_columns_in_context` appears in `contracts/gates.py`, is read by
`gates/rules.py`, and is constructed by hand in tests. **No production code ever
sets it.** The egress gate the classifier was built to feed cannot fire.

So `classify_sensitivity` has exactly one live consumer: `discovery/support.py`,
which drops PII columns from the feature pool. That is a model-quality decision —
a model keyed to an identifier memorises rows and collapses in production — not a
privacy control, and it has been described as the latter throughout.

This needs a decision, and it is not mine to make quietly: **wire the signal up,
or delete the rule.** A rule that looks like a control and is not one is worse
than no rule, because it reads as covered.

## Sequence

1. Decide `pii_columns_in_context`: wire or delete.
2. Presidio as the recognizer framework, existing checksums registered as custom
   recognizers, Turkish spaCy as the NLP engine. Nothing lost, one dependency.
3. The value-level bridge: sample, detect, aggregate. Write the aggregation rule
   deliberately — it decides the accuracy of everything above it.
4. Benchmark against ai4privacy for what it covers. Report the Turkish gap as a
   gap.
5. Agent proposal for column-name semantics, inside the existing propose/decide
   contract.

Steps 1 and 2 are cheap and strictly better than today. Step 4 is the one that
tells us whether any of it worked, and step 5 is the one worth arguing about.

## Offline constraint

All of the above must run with no network at import and none at inference.
Presidio, spaCy and transformers all support loading from a local path; the
weights would be vendored once and pinned, the same way the sandbox image is.
This has not been verified by running it — it is the first thing to check before
committing to the dependency, because a package that phones home at import is
disqualified regardless of how well it scores. Deepchecks was rejected on exactly
that basis during the earlier framework review.
