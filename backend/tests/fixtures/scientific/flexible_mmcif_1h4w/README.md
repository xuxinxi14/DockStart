# 1H4W external flexible-receptor acceptance

This fixture intentionally contains metadata only. It does not redistribute
the RCSB/wwPDB coordinate file or the RCSB ModelServer BEN SDF.

Obtain the exact two sources pinned in `source_manifest.json`, verify their
declared content identities, then run:

```powershell
python scripts/verify_flexible_mmcif_1h4w.py `
  --mmcif C:\path\to\1H4W.cif `
  --ligand-sdf C:\path\to\BEN_A.sdf `
  --python E:\DockStart\resources\python\python.exe `
  --vina E:\DockStart\resources\vina\vina.exe
```

The receptor uses an exact raw-file SHA256. RCSB ModelServer appends a
request-specific job id, timestamp, and timing values to the BEN SDF, so its
raw response SHA256 is recorded only as retrieval provenance. The blocking
BEN identity is the LF-normalized first mol block through its sole `M  END`
line, together with an exact allowlist of stable and dynamic ModelServer
properties. Molecular bytes, stable query metadata, extra records, unknown
properties, or malformed SDF structure remain blocking.

The gate is not a metadata-only or optional-Meeko smoke test. It creates a
temporary DockStart project and exercises the public project APIs for:

1. pinned external source and toolchain gates;
2. raw receptor and ligand import;
3. real RDKit/Meeko BEN preparation;
4. schema-v2 mmCIF identity and verified Gemmi bridge;
5. explicit global altloc decisions, CYX templates, and BEN deletion;
6. real rigid/flex/receptor-JSON preparation for `A:192` and `A:221:A`;
7. pinned box and Vina parameters;
8. run preparation, real Vina execution, score analysis, flexible movement
   analysis, and Markdown report export.

Every generated artifact stays inside the temporary project and is removed at
the end. A Meeko, Vina, movement, report, or cleanup failure is blocking. The
verifier never writes substitute PDBQT, log, score, movement, or report data.
