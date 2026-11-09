# The `echr:` ontology

- `echr.ttl` (3.8.0-timeline): the extraction schema for domestic procedural
  history. Four event classes (`DomesticProceeding`, `ProsecutorialReview`,
  `AdministrativeAction`, `EnforcementAction`), the chain properties
  (`followsProceeding`, `partOfProceeding`, `occursDuring`), closed
  vocabularies (instance level, outcome, direction, proceeding type, matter,
  authority kind, party side), and the timeline extension (dated
  circumstances; the Health, Immigration and Enforcement matters; the
  Strasbourg application). Every class and property carries a
  `skos:definition`, and a `skos:scopeNote` where the boundary is contestable.
- `echr-shapes.ttl`: the SHACL shapes graphs are validated and repaired against.

The pipeline snapshots both into each run's directory. `ontology.env` and
`ontology_vllm.env` are OntoCast settings (gitignored: they hold credentials).
