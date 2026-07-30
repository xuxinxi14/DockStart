# 1IEP Vina evaluation-mode acceptance fixture

This fixture stores provenance and acceptance oracles only. It does not copy
the third-party 1IEP PDBQT inputs into DockStart and it is not included in a
DockStart installer.

The two inputs are the prepared `1iep_receptor.pdbqt` and
`1iep_ligand.pdbqt` files published in the AutoDock Vina v1.2.7
`example/python_scripting` directory. The external verifier requires the
caller to provide both files and rejects any size or SHA256 mismatch. It does
not download inputs, synthesize PDBQT, or accept a skipped Vina execution.

The fixture verifies software behavior, not biological validity:

- one seeded global docking run applies non-default `max_evals = 500`,
  `min_rmsd = 0.5`, `spacing = 0.5`, and `verbosity = 2`, while excluding
  the saved score-only `unbound_energy` value from its real config;
- `score_only` evaluates the frozen input pose and must not publish a new pose;
- modern `local_only` must run an input `score_only` baseline before local
  optimization and preserve both stages;
- the real bundled Vina v1.2.7 capability evidence must admit the requested
  expert options;
- the same request with capability evidence removed must be rejected
  fail-closed.

Run it from the repository root after obtaining the two exact upstream files:

```powershell
resources\python\python.exe scripts\verify_vina_evaluation_1iep.py `
  --receptor "<path-to-1iep_receptor.pdbqt>" `
  --ligand "<path-to-1iep_ligand.pdbqt>" `
  --vina resources\vina\vina.exe
```

The command writes one structured JSON document to stdout. The global,
score-only, and local-only runs are all mandatory. Any missing input, hash
drift, Vina mismatch, capability-gate failure, process error, or result oracle
mismatch returns a non-zero exit code; there is no optional or skipped success
state.

Upstream project: <https://github.com/ccsb-scripps/AutoDock-Vina/tree/v1.2.7>

AutoDock Vina source license: Apache License 2.0. The 1IEP structure retains
its original structural-data and publication attribution. This fixture is
only a reproducible regression gate and must not be interpreted as evidence
of binding, efficacy, or experimental validation.
