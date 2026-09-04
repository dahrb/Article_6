"""The common comparison form every condition is mapped into before scoring.

A flat list of proceedings, each carrying the fields named in
docs/jurix_plan.md §1 -- deciding body, date, instance level, outcome, order and
supporting quote -- plus the per-step participants and any custodial measure.
All comparative measures are computed on this form, which is what lets O2 and a
no-ontology baseline be scored on the same terms.

`follows` is the field the chain outcome is computed on, and it is here because
the alternative was measuring the chain through `order` alone. `order` is a
single integer: it can express a line and nothing else. Measured on the
2026-08-24 ten-document set, 8 of 10 documents contain more than one
INDEPENDENT track -- a criminal prosecution beside a civil claim, the
applicant's appeal beside the State's -- and 2 contain a decision reviewed by
two later steps. A linear index cannot represent either, so scoring O2's
echr:followsProceeding graph through `order` discards precisely the structure
the ontology exists to encode. Asking both conditions for the review relation
directly puts the chain back in the comparison without favouring either: it is
a projection for O2 and an ordinary request for O1.

`parties` and `custodial_measure` are here because the earlier form measured
only a fifth of what O2 produces, and comparing conditions on a projection that
discards most of the treatment is not a fair test of the treatment. They are
the two richest things O2 extracts that a schema-light condition can also
reasonably be ASKED for -- which is the constraint that decides what belongs in
this form. Anything O1 is not asked for cannot be scored, because scoring a
condition on output it was never requested to produce is the mirror image of
the strawman objection.

Every field except `order` is optional, and that is a measurement decision
rather than laxness: a condition that fails to produce a deciding body must be
able to say so, because "did not extract this field" is exactly the signal the
comparison is looking for. Coercing a missing value into a placeholder would
hide the difference the study exists to measure.

Deliberately NOT in this form: closed-vocabulary terms, IRIs, node identity,
reified participation structure. Those are properties of the O2 artefact and
are reported for O2 alone, framed as describing the artefact rather than
comparing conditions. `parties` flattens O2's Participation reification into a
string per participant precisely so the comparison does not require the
baseline to have reified anything.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NormalisedProceeding(BaseModel):
    order: int = Field(
        description=(
            "1-based position in the procedural chain, earliest first. Where "
            "two proceedings run in parallel tracks (a criminal and a civil "
            "case over the same facts), order them by date and give each its "
            "own number; the chain is a sequence, not a tree."
        )
    )
    deciding_body: str | None = Field(
        default=None,
        description=(
            "The court, tribunal, prosecutor's office or administrative body "
            "that decided this step, named as the document names it. Null if "
            "the document does not say."
        ),
    )
    decision_date: str | None = Field(
        default=None,
        description=(
            "The date of the decision, as an ISO date (YYYY-MM-DD) where the "
            "document gives a full date, otherwise YYYY-MM or YYYY. Null if "
            "the document does not say."
        ),
    )
    instance_level: str | None = Field(
        default=None,
        description=(
            "Where this step sits in the hierarchy -- first instance, appeal, "
            "cassation, constitutional review, investigative, administrative "
            "review, and so on. Free text: this field is NOT drawn from a "
            "fixed vocabulary, because two of the three conditions were never "
            "given one."
        ),
    )
    outcome: str | None = Field(
        default=None,
        description=(
            "What the body decided. Free text, for the same reason as "
            "instance_level. Null if the document does not say."
        ),
    )
    supporting_quote: str | None = Field(
        default=None,
        description=(
            "A verbatim span from the source document evidencing this "
            "proceeding. Null where none is available."
        ),
    )
    parties: list[str] | None = Field(
        default=None,
        description=(
            "Who took part in THIS step and on which side, one entry each, "
            'written as "name (side)" -- e.g. "the applicant (initiating)", '
            '"the State (responding)". Roles are per-step: a party who '
            "responds at trial and initiates on appeal appears differently in "
            "each. Null where the document does not say."
        ),
    )
    follows: list[int] | None = Field(
        default=None,
        description=(
            "The `order` values of the earlier entries this step reviews or "
            "continues -- an appeal names the judgment it appeals, a cassation "
            "ruling names that appeal. Usually one; several where a single step "
            "disposes of two earlier ones. Null for a step that starts a track "
            "rather than continuing one."
        ),
    )
    custodial_measure: str | None = Field(
        default=None,
        description=(
            "Any deprivation of liberty or restriction of legal capacity "
            "applied or maintained during this step -- police custody, remand, "
            "psychiatric confinement, guardianship. Null where none is "
            "mentioned."
        ),
    )


class NormalisedDocument(BaseModel):
    proceedings: list[NormalisedProceeding]
