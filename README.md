# ECHR Art. 6 Seed Workflow

This workspace now includes a simplified owlready2-based ontology flow that keeps schema and metadata artifacts separate.

## Outputs

- `ontology/seed.ttl`: simplified T-Box schema in Turtle
- `ontology/metadata.ttl`: A-Box metadata seed graph in Turtle

## Create Schema

```powershell
c:/Postdoc/Article_6/.venv/Scripts/python.exe c:/Postdoc/Article_6/ontology/create_schema.py
```

## Ingest Metadata

```powershell
c:/Postdoc/Article_6/.venv/Scripts/python.exe c:/Postdoc/Article_6/ontology/ingest_metadata.py
```

By default, metadata ingestion reads only `data/sample_metadata.parquet` and enriches judge nodes from `data/additional_data/judges_processed.json` when IDs or matching names are available.

Fuseki and GraphFlo are intentionally out of scope for this workflow.
