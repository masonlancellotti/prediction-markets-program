# Relationship Files

Relationship files are hand-curated edge files loaded from this folder. In v1 they may be JSON-compatible YAML, which keeps the scanner usable even when optional YAML dependencies are absent.

Each file can contain:

- `edges`: pairwise semantic relationships between markets.
- `exclusion_sets`: hyperedges for mutually exclusive outcomes.

Rules:

- Use globally unique market ids such as `venue:native_id`.
- Prefer `manual` source for v1 relationships.
- Put mutual exclusion in `exclusion_sets`, not pairwise edges.
- Use `AMBIGUOUS` when wording is related but not safe to constrain.
- Keep rationale and evidence snippets short but auditable.
- Never encode direct recommendations or execution instructions.

