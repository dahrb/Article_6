# The `echr:` ontology

- `echr.ttl` (4.1.0-sets): the schema for domestic procedural history.
  `echr:ProceedingSet` groups a set's events (`inProceedingSet`, `isPending`,
  `enforcesDecision`); the event classes (`DomesticProceeding`,
  `ProsecutorialReview`, `AdministrativeAction`, `EnforcementAction`) carry
  dates, the deciding `DomesticAuthority`, participations with a party side,
  and the closed vocabularies: instance level, outcome, direction, proceeding
  type, matter, authority kind, `appealedBy`, `costsBorneBy`. `reviewsDecision`
  links a decision to the one it rules on; `followsProceeding` is the derived
  order. Every class and property carries a `skos:definition`, and a
  `skos:scopeNote` where the boundary is contestable. The v2 chain
  properties (`partOfProceeding`, `occursDuring`) are deprecated and kept only
  so older graphs still read.
- `echr-shapes.ttl`: the SHACL shapes graphs are validated and repaired against.

The pipeline snapshots both into each run's directory. `ontology.env` and
`ontology_vllm.env` are OntoCast settings (gitignored: they hold credentials).
