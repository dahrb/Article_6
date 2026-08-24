"""Ontology-condition arms of the JURIX study: O1 and the normalisation step.

O2 -- the full-ontology condition -- is the existing pipeline under
`art6/ontology/`. This package holds the schema-light condition that
deliberately has no ontology, and the normalisation that makes both comparable.

See docs/jurix_plan.md §1. The design constraint the whole package exists to
satisfy: half the evaluation stack (SHACL, closed-vocabulary conformance, IRI
discipline) is ontology-dependent and therefore meaningless for a baseline that
was never asked to produce it. Scoring the baseline on a rubric only the
treatment can satisfy is a demonstration, not an experiment.
"""
