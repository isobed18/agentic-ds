# understanding_v0

Measures whether the system understands a mixed-source corpus it was handed:
routing, primary keys, joins, cardinality, and which document content is
trustworthy. `assurance_v0` asks a later question — whether the gate makes
defensible decisions. This one asks whether there was anything sound to decide
*about*.

## The corpus is real, and not committed

`fetch.py` downloads it; nothing here is generated, and nothing is vendored.

| Source | What it is |
|---|---|
| [MovieLens `ml-latest-small`](https://grouplens.org/datasets/movielens/) | 4 related CSVs — 9,742 movies, 100,836 ratings, 3,683 tags, 9,742 links |
| `README.txt` | ships with MovieLens; real prose, not a table |
| [arXiv 2307.09985v3](https://arxiv.org/abs/2307.09985) | 25-page paper analysing *this dataset*, real text layer, tables and figures |

Synthetic tables join the way whoever wrote the generator imagined they would,
so a system can score well on structure that was never in doubt. Real data
argues back. MovieLens contains, without anyone arranging it:

- **A 1:1 next to an N:1.** `links → movies` and `ratings → movies` use the same
  column and both have 100% overlap. The only thing separating them is whether
  the child keys are distinct. Reading "the key matched" as "N:1" gets one of
  them wrong every time — this is the sharpest signal in the benchmark.
- **An entity with no table.** `userId` appears in `ratings` and `tags` and has
  no parent anywhere. A discovery that only looks for child-to-parent edges
  never notices the two tables share a key space.
- **Real mess.** 8 missing `tmdbId`, a pipe-delimited `genres`, a release year
  buried in `title`, epoch integers for timestamps.

MovieLens is free for research use under GroupLens' terms and must not be
redistributed, which is the other reason `fetch.py` downloads rather than
vendors.

## The answer key is measured, not written

`ground_truth.json` is computed from the downloaded files. An answer key someone
typed is an opinion; one computed from the data is a fact, and it stays correct
if the upstream file changes. Re-derive it any time with `--measure-only`.

## Running it

```bash
python benchmarks/understanding_v0/fetch.py --out data/benchmark-understanding
```

Point a Data project at that directory, let intake and schema discovery finish,
then save the run's measured relationships and score them:

```bash
python benchmarks/understanding_v0/score.py \
  --truth data/benchmark-understanding/ground_truth.json \
  --run relationships.json
```

`--run` accepts a bare list or a staging-workspace payload.

## Reading the score

Three numbers, reported separately because they fail for different reasons and
an average hides which one broke.

- **Edge recall / precision.** Recall alone rewards proposing every column pair;
  precision is what stops it. A join listed under `false_relationships` is
  counted separately again — proposing a coincidence the corpus documents as one
  is a worse error than proposing an unlisted but plausible join.
- **Cardinality accuracy**, over matched edges only. Being wrong about an edge
  you never found is already counted as a miss.
- **Overlap agreement**, within 0.01. These are counts, not estimates; being
  approximately right about an exact quantity is being wrong about it.

## Known-failing on purpose

`README.txt` is prose and should route to documents. The architecture report
records that TXT is currently treated as delimited text, so it is expected to be
misrouted today. `ground_truth.json` marks it
`currently_misrouted_as_structured`, so the gap stays visible instead of being
quietly absorbed into the expected result.

## The synthetic corpus

Traps that real data does not contain — an injected target leak, checksum-valid
national ids, deliberate orphan rows — live in
`tests/fixtures/synthetic_corpus.py` and are used by tests, not by this
benchmark. Scoring capability against invented structure would measure the
generator.
