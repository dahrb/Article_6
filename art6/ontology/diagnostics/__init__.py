"""Evaluation and reporting tools, run manually against completed extraction runs.

Nothing here is on the extraction pipeline's execution path -- `run_experiment.sh`
never imports this package. Each module here answers a question about output
already produced (how good is it, what did repair change, how would a document
chunk) rather than producing output itself. Safe to skip when reviewing whether
the pipeline itself is correct; read these when reviewing the evaluation reports
built from their output.
"""

__all__ = [
    "chunk_probe",
    "quality_metrics",
    "repair_impact",
    "validate_dates",
    "validate_source_quotes",
]
