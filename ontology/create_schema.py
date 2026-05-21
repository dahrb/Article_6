"""
Script to create the full ECHR Art. 6 seed schema programmatically
and serialize it to Turtle.

Last Updated:
20.05.26

Status:
Done

History:
v1_0 - simplified schema generation
v2_0 - full seed schema generation from Python definitions (classes, properties, vocab)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from owlready2 import AllDisjoint, DataProperty, ObjectProperty, Thing, get_ontology, locstr
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD, FOAF
from rdflib.namespace import DCTERMS, PROV

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
ONTOLOGY_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl"
ONTOLOGY_BASE_IRI = f"{ONTOLOGY_IRI}#"
LICENSE_IRI = URIRef("https://creativecommons.org/licenses/by/4.0/")

DEFAULT_SCHEMA_TTL = SCRIPT_DIR / "seed.ttl"

# Initialize ontology module
onto = get_ontology(ONTOLOGY_IRI)
onto.base_iri = ONTOLOGY_BASE_IRI

# Set Ontology Metadata
with onto:
    onto.label = [locstr("ECHR Art. 6 Ontology", "en")]
    onto.versionInfo = [locstr("3.0")]
    onto.comment = [locstr("An ontology for Art.6 Cases heard at the ECHR", "en")]

# ---------------------------------------------------------------------------
# External Vocabulary References
# ---------------------------------------------------------------------------
# Load external vocabularies for alignment
foaf_onto = get_ontology("http://xmlns.com/foaf/0.1/")
prov_onto = get_ontology("http://www.w3.org/ns/prov#")
wd_onto = get_ontology("http://www.wikidata.org/entity/")
skos_onto = get_ontology("http://www.w3.org/2004/02/skos/core#")

# Reference key external classes
with foaf_onto:
    class Person(Thing):
        pass
    class Agent(Thing):
        pass
    class Organization(Thing):
        pass

with prov_onto:
    class Entity(Thing):
        pass

with skos_onto:
    class Concept(Thing):
        pass

with wd_onto:
    class Q712597(Thing):  # "article of law"
        pass
    class Q327000(Thing):  # "legal decision"
        pass
    class Q3769186(Thing): # legal judgment
        pass
    class Q7748(Thing):  # "law"
        pass
    class Q1668641(Thing):  # "national law"
        pass
    class Q4394526(Thing):  # "international law"
        pass
    class Q56759633(Thing): # application
        pass
    class Q2334719(Thing): # legal case
        pass
    class Q3624078(Thing): # sovereign state 
        pass
    class Q357147(Thing): #court chamber 
        pass
    class Q16533(Thing): #judge
        pass
    class Q12648988(Thing): #appellant
        pass
    class Q48277(Thing): #gender
        pass
    class Q2478386(Thing): #legal system
        pass
    class Q2628882(Thing): #legal status
        pass
    class Q581445(Thing): #social vulnerability
        pass
    class Q88483334(Thing): #financial status
        pass
    class Q40348(Thing):  # "lawyer"
        pass

# Reference Dublin Core properties
dcterms_onto = get_ontology("http://purl.org/dc/terms/")
with dcterms_onto:
    class title(DataProperty):
        pass
    class date(DataProperty):
        pass
    class language(DataProperty):
        pass
    class subject(DataProperty):
        pass
    class source(DataProperty):
        pass
    class isVersionOf(ObjectProperty):
        pass
    class references(ObjectProperty):
        pass
    class type(ObjectProperty):
        pass
    

# Reference FOAF properties
with foaf_onto:
    class name(DataProperty):
        pass

# ---------------------------------------------------------------------------
# Schema Definition - Entity Classes (Native owlready2 with Alignments)
# ---------------------------------------------------------------------------
with onto:
    
    class Application(Thing):
        pass
    Application.is_a.append(wd_onto.Q56759633)    
    Application.label = ["Application"]
    Application.comment = ["Metadata-derived identifier node for application numbers. Primarily populated from case_appno, cited_appno, and secondary_appno; canonical values are attached via application_number_normalized."]

    class Article6Limb(Thing):
        pass
    Article6Limb.label = ["Article 6 Limb"]
    Article6Limb.comment = ["Metadata-derived controlled concept from article_6_limb (e.g., Civil, Criminal, Mixed, Constitutional, Unspecified)."]

    class CaseDocument(Thing):
        pass
    CaseDocument.is_a.append(wd_onto.Q2334719)
    CaseDocument.label = ["Case"]
    CaseDocument.comment = ["Primary case record class. Most case-level assertions are metadata-derived from one row in the processed Art. 6 dataset."]

    class Decision(CaseDocument):
        pass
    Decision.is_a.append(wd_onto.Q327000)
    Decision.label = ["Decision"]
    Decision.comment = ["Metadata-derived case subtype for rows where source indicates a decision document."]

    class Judgment(CaseDocument):
        pass
    Judgment.is_a.append(wd_onto.Q3769186)
    Judgment.label = ["Judgment"]
    Judgment.comment = ["Metadata-derived case subtype for rows where source indicates a judgment document."]
    
    class Law(Thing):
        pass
    Law.is_a.append(wd_onto.Q7748)
    Law.label = ["Law Source"]
    Law.comment = ["General legal source class aligned to wd:Q7748 (law); used as a target for legal citation assertions."]
    
    class LegalRepresentative(Thing):
        pass
    LegalRepresentative.is_a.append(foaf_onto.Person)
    LegalRepresentative.is_a.append(wd_onto.Q40348)
    LegalRepresentative.label = ["Legal Representative"]
    LegalRepresentative.comment = ["Person acting as legal counsel for an applicant or party (lawyer-aligned entity)."]

    class DomesticLaw(Law):
        pass
    DomesticLaw.is_a.append(wd_onto.Q1668641)
    DomesticLaw.label = ["Domestic Law"]
    DomesticLaw.comment = ["Aligned to wd:Q1668641 (national law)."]

    class InternationalLaw(Law):
        pass
    InternationalLaw.is_a.append(wd_onto.Q4394526)
    InternationalLaw.label = ["International Law"]
    InternationalLaw.comment = ["Aligned to wd:Q47659 (international law)."]

    class ImportanceLevel(Thing):
        pass
    ImportanceLevel.label = ["Importance Level"]
    ImportanceLevel.comment = ["Metadata-derived controlled importance score from the importance field (values observed: 1-4)."]
    
    class ConventionArticle(Thing):
        pass
    ConventionArticle.is_a.append(wd_onto.Q712597)
    ConventionArticle.label = ["Convention Article"]
    ConventionArticle.comment = ["Convention article concept aligned to wd:Q712597. Primarily metadata-derived from article and conclusion references (e.g., 6, 6-1, P1-1)."]

    class Country(Thing):
        pass
    Country.is_a.append(wd_onto.Q3624078)
    Country.label = ["Country"]
    Country.comment = ["Country concept primarily metadata-derived from respondent and country_name values, plus judge authority data, and aligned to Wikidata when available."]

    class CourtFormation(Thing):
        pass
    CourtFormation.is_a.append(foaf_onto.Organization)
    CourtFormation.label = ["Court Formation"]
    CourtFormation.comment = ["Metadata-derived court body from originatingbody (e.g., Court (Grand Chamber), Court (First Section), Commission). Aligned to foaf:Organization."]

    class ChamberType(Thing):
        pass
    ChamberType.is_a.append(wd_onto.Q357147)
    ChamberType.label = ["Chamber Type"]
    ChamberType.comment = ["Metadata-driven normalized grouping from court_level values (e.g., Committee, Chamber, Grand Chamber, Commission)."]

    class Judge(Thing):
        pass
    Judge.is_a.append(wd_onto.Q16533)
    Judge.is_a.append(foaf_onto.Person)
    Judge.label = ["Judge"]
    Judge.comment = ["Judge entity primarily metadata-derived from judges and judges_id, then normalized for reuse across cases. Aligned to foaf:Person."]

    class Party(Thing):
        pass
    Party.is_a.append(wd_onto.Q12648988)
    Party.is_a.append(foaf_onto.Agent)
    Party.label = ["Party"]
    Party.comment = ["Applicant/appellant party entity for a case, typically inferred from metadata-derived appellant information. Aligned to foaf:Agent."]

    class Gender(Thing):
        pass
    Gender.is_a.append(wd_onto.Q48277)
    Gender.label = ["Gender"]
    Gender.comment = ["Controlled gender vocabulary used for party-level gender assertions."]

    class JudgmentType(Thing):
        pass
    JudgmentType.label = ["Judgment Type"]
    JudgmentType.comment = ["Metadata-derived controlled document type from judgment_type values (observed as a small closed set)."]

    class LegalFinding(Thing):
        pass
    LegalFinding.is_a.append(prov_onto.Entity)
    LegalFinding.label = ["Legal Finding"]
    LegalFinding.comment = ["Structured legal conclusion class capturing result statements (violation/non-violation) derived from decision outcomes."]

    class Violation(LegalFinding):
        pass
    Violation.label = ["Violation"]
    Violation.comment = ["A judicial finding that a violation of a Convention Article occurred."]

    class NonViolation(LegalFinding):
        pass
    NonViolation.label = ["Non-Violation"]
    NonViolation.comment = ["A judicial finding that no violation of a Convention Article occurred."]

    class LawSystem(Thing):
        pass
    LawSystem.is_a.append(wd_onto.Q2478386)
    LawSystem.label = ["Law System"]
    LawSystem.comment = ["Metadata-derived legal tradition from law_system (observed values include Civil, Common, and Mixed)."]

    class SeparateOpinionIndicator(Thing):
        pass
    SeparateOpinionIndicator.label = ["Separate Opinion Indicator"]
    SeparateOpinionIndicator.comment = ["Metadata-derived indicator from separateopinion showing whether a separate opinion is present."]

    class OperativeProvision(Thing):
        pass
    OperativeProvision.label = ["Operative Provision"]
    OperativeProvision.comment = ["The legally binding 'Holds that...' section of the judgment. Contains the final judicial voting outcomes."]
    
    class LegalStatus(Thing):
        pass
    LegalStatus.is_a.append(wd_onto.Q2628882)
    LegalStatus.label = ["Legal Status"]
    LegalStatus.comment = ["Classification of the applicant entity."]

    class VulnerabilityStatus(Thing):
        pass
    VulnerabilityStatus.is_a.append(wd_onto.Q581445)
    VulnerabilityStatus.label = ["Vulnerability Status"]
    VulnerabilityStatus.comment = ["Specific vulnerabilities identified in the case facts."]

    class EconomicStatus(Thing):
        pass
    EconomicStatus.is_a.append(wd_onto.Q88483334) 
    EconomicStatus.label = ["Economic Status"]
    EconomicStatus.comment = ["Indicators of the applicant's financial standing."]
    
    class Keyword(Thing):
        pass
    Keyword.is_a.append(skos_onto.Concept)
    Keyword.label = ["Thesaurus Keyword"]
    Keyword.comment = ["A controlled HUDOC thesaurus concept. Aligned to skos:Concept."]
    
    # Disjoint constraint
    AllDisjoint([Judgment, Decision])


# ---------------------------------------------------------------------------
# Schema Definition - Datatype Properties
# ---------------------------------------------------------------------------
with onto:
    
    class hasApplicationNumber(DataProperty):
        pass
    hasApplicationNumber.domain = [Application]
    hasApplicationNumber.range = [str]
    hasApplicationNumber.label = ["application number"]
    hasApplicationNumber.comment = ["Metadata-derived canonical application number literal on Application. Built from normalized values extracted from case_appno/cited_appno/secondary_appno."]

    #do judges first 

    class hasTenureBeginYear(DataProperty):
        domain = [Judge]
        range = [str]
    hasTenureBeginYear.label = ["has tenure begin year"]
    hasTenureBeginYear.comment = ["Judge career metadata: year in which Court tenure began."]

    class hasTenureEndYear(DataProperty):
        domain = [Judge]
        range = [str]
    hasTenureEndYear.label = ["has tenure end year"]
    hasTenureEndYear.comment = ["Judge career metadata: year in which Court tenure ended."]

    class isSectionPresident(DataProperty):
        domain = [Judge]
        range = [bool]
    isSectionPresident.label = ["is section president"]
    isSectionPresident.comment = ["Judge profile metadata flag indicating whether the person served as Section President."]

    class hasPresidencyBeginYear(DataProperty):
        domain = [Judge]
        range = [str]
    hasPresidencyBeginYear.label = ["has presidency begin year"]
    hasPresidencyBeginYear.comment = ["Judge leadership metadata: presidency start year (expected only when isSectionPresident is true)."]

    class hasPresidencyEndYear(DataProperty):
        domain = [Judge]
        range = [str]
    hasPresidencyEndYear.label = ["has presidency end year"]
    hasPresidencyEndYear.comment = ["Judge leadership metadata: presidency end year (expected only when isSectionPresident is true)."]

    class hasJudgeId(DataProperty):
        pass
    hasJudgeId.domain = [Judge]
    hasJudgeId.range = [int]
    hasJudgeId.label = ["has Judge Id"]
    hasJudgeId.comment = ["Metadata-derived stable judge identifier from judges_id; preferred key for deduplicating judge entities across rows."]

    class hasRepresentativeName(DataProperty):
        domain = [LegalRepresentative]
        range = [str]
    hasRepresentativeName.label = ["has representative name"]
    hasRepresentativeName.comment = ["Metadata-derived legal representative name string; used when a dedicated representative identifier is unavailable."]
    
    class hasApplicantName(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasApplicantName.label = ["has Applicant Name"]
    hasApplicantName.comment = ["Metadata-derived applicant/appellant name from appellant; preserved as raw text when entity resolution is incomplete."]

    class hasCaseName(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasCaseName.label = ["has Case Name"]
    hasCaseName.comment = ["Metadata-derived case title from case_name; stored as the primary human-readable label for the case."]

    class hasCaseTextPath(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasCaseTextPath.label = ["has Case Text Path"]
    hasCaseTextPath.comment = ["Metadata-derived relative file path to the source case text artifact, retained for provenance and reproducibility."]

    class hasEcli(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasEcli.label = ["has Ecli"]
    hasEcli.comment = ["Metadata-derived ECLI citation identifier for legal reference and cross-system interoperability."]

    class hasItemId(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasItemId.label = ["has Item Id"]
    hasItemId.comment = ["Metadata-derived HUDOC item identifier (itemid); canonical external key used for record identity and IRI minting."]

    class hasJudgmentDate(DataProperty):
        domain = [CaseDocument]
        range = [date]
    hasJudgmentDate.label = ["has Judgment Date"]
    hasJudgmentDate.comment = ["Metadata-derived date from judgementdate; expected to parse to xsd:date when valid."]

    class hasKeywordCode(DataProperty):
        domain = [Keyword]
        range = [str]
    hasKeywordCode.label = ["has Keyword Code"]
    hasKeywordCode.comment = ["Metadata-derived raw thesaurus code string from kpthesaurus, retained as provenance for downstream mapping checks."]

    class hasLanguageCode(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasLanguageCode.label = ["has Language Code"]
    hasLanguageCode.comment = ["Metadata-derived language ISO code from languageisocode (observed ENG/FRE in current sample)."]

    class hasYear(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasYear.label = ["has Year"]
    hasYear.comment = ["Metadata-derived case year value from year, modeled as a gYear-compatible literal."]

    class hasViolationGrounds(DataProperty):
        domain = [OperativeProvision]
        range = [str]
    hasViolationGrounds.label = ["has Violation Grounds"]
    hasViolationGrounds.comment = ["Textual rationale describing why a violation was found (e.g., lack of an independent and impartial tribunal)."]
    
    class hasConclusionText(DataProperty):
        domain = [CaseDocument]
        range = [str]
    hasConclusionText.label = ["has Conclusion Text"]
    hasConclusionText.comment = ["Raw, disorganized conclusion text from the metadata, preserved as a literal string to capture exact translations and nuances."]
# ---------------------------------------------------------------------------
# Schema Definition - Object Properties
# ---------------------------------------------------------------------------
with onto:
    class hasArticle6Limb(ObjectProperty):
        pass
    hasArticle6Limb.domain = [CaseDocument]
    hasArticle6Limb.range = [Article6Limb]
    hasArticle6Limb.label = ["has Article 6 Limb"]
    hasArticle6Limb.comment = ["Metadata-derived mapping from article_6_limb to controlled limb concepts (Civil, Criminal, Mixed, Constitutional, Unspecified)."]

    class hasImportanceLevel(ObjectProperty):
        domain = [CaseDocument]
        range = [ImportanceLevel]
    hasImportanceLevel.label = ["has Importance Level"]
    hasImportanceLevel.comment = ["Metadata-derived link from a case to its ECtHR importance level concept (1-4)."]

    class hasRespondentState(ObjectProperty):
        pass
    hasRespondentState.domain = [CaseDocument]
    hasRespondentState.range = [Country]
    hasRespondentState.label = ["has Respondent State"]
    hasRespondentState.comment = ["Metadata-derived respondent state link from respondent, normalized to country entities and aligned to Wikidata where available."]

    class concernsArticle(ObjectProperty):
        pass
    concernsArticle.domain = [CaseDocument]
    concernsArticle.range = [ConventionArticle]
    concernsArticle.label = ["concerns Article"]
    concernsArticle.comment = ["Metadata-derived article relation from article tokens (e.g., 6, 6-1, P1-1), linked to ConventionArticle concepts."]

    class hasNationality(ObjectProperty):
        pass
    hasNationality.domain = [Party]
    hasNationality.range = [Country]
    hasNationality.label = ["has Nationality"]
    hasNationality.comment = ["Country nationality relation for Party, aligned to country entities (Wikidata-backed where resolvable)."]

    class hasCourtFormation(ObjectProperty):
        pass
    hasCourtFormation.domain = [CaseDocument]
    hasCourtFormation.range = [CourtFormation]
    hasCourtFormation.label = ["has Court Formation"]
    hasCourtFormation.comment = ["Metadata-derived court formation from originatingbody, normalized into reusable CourtFormation entities."]

    class hasChamberType(ObjectProperty):
        pass
    hasChamberType.domain = [CaseDocument]
    hasChamberType.range = [ChamberType]
    hasChamberType.label = ["has Chamber Type"]
    hasChamberType.comment = ["Metadata-derived normalized chamber type from court_level values via controlled mapping."]

    class hasGender(ObjectProperty):
        pass
    hasGender.domain = [Party]
    hasGender.range = [Gender]
    hasGender.label = ["has Gender"]
    hasGender.comment = ["IRI-based controlled gender value inferred from explicit evidence when available."]

    class hasLegalRepresentative(ObjectProperty):
        domain = [CaseDocument]
        range = [LegalRepresentative]
    hasLegalRepresentative.label = ["has Legal Representative"]
    hasLegalRepresentative.comment = ["Links a case to its legal representative entity when representative information is available in metadata/enrichment inputs."]

    class hasFinding(ObjectProperty):
        domain = [CaseDocument]
        range = [LegalFinding]
    hasFinding.label = ["has Finding"]
    hasFinding.comment = ["Links a case to structured legal outcome nodes (Violation/NonViolation and related findings)."]

    class findingRefersToArticle(ObjectProperty):
        domain = [LegalFinding]
        range = [ConventionArticle]
    findingRefersToArticle.label = ["finding refers to Article"]
    findingRefersToArticle.comment = ["Connects a legal finding to the specific Convention article it evaluates."]

    class hasSeparateOpinion(ObjectProperty):
        pass
    hasSeparateOpinion.domain = [CaseDocument]
    hasSeparateOpinion.range = [SeparateOpinionIndicator]
    hasSeparateOpinion.label = ["has Separate Opinion"]
    hasSeparateOpinion.comment = ["Metadata-derived separate-opinion flag from separateopinion, mapped to controlled indicator individuals."]

    class hasOperativeProvision(ObjectProperty):
        domain = [CaseDocument]
        range = [OperativeProvision]
    hasOperativeProvision.label = ["has Operative Provision"]
    hasOperativeProvision.comment = ["Links a case to operative, legally binding outcome clauses extracted/structured for reasoning."]

    class provisionRefersToArticle(ObjectProperty):
        pass
    provisionRefersToArticle.domain = [OperativeProvision]
    provisionRefersToArticle.range = [ConventionArticle]
    provisionRefersToArticle.label = ["provision Refers To Article"]

    class hasLawSystem(ObjectProperty):
        pass
    hasLawSystem.domain = [CaseDocument]
    hasLawSystem.range = [LawSystem]
    hasLawSystem.label = ["has Law System"]
    hasLawSystem.comment = ["Metadata-derived legal system classification from law_system (e.g., Common, Civil, Mixed)."]

    class citesApplication(ObjectProperty):
        domain = [CaseDocument]
        range = [Application]
    citesApplication.label = ["cites Application"]
    citesApplication.comment = ["Metadata-derived citation edge from cited_appno to normalized Application entities for application-level citation graphs."]

    class citesCase(ObjectProperty):
        domain = [CaseDocument]
        range = [CaseDocument]
    citesCase.label = ["cites Case"]
    citesCase.comment = ["Resolved citation edge between case documents when cited application numbers map to known CaseDocument entities."]

    class hasApplication(ObjectProperty):
        domain = [CaseDocument]
        range = [Application]
    hasApplication.label = ["has Application"]
    hasApplication.comment = ["Metadata-derived relation from case_appno linking a case to one or more normalized Application entities."]
    
    class hasJudgmentType(ObjectProperty):
        domain = [CaseDocument]
        range = [JudgmentType]
    hasJudgmentType.label = ["has Document Type"]
    hasJudgmentType.comment = ["Metadata-derived document type from judgment_type, represented as a controlled concept for filtering and grouping."]

    class hasJudge(ObjectProperty):
        domain = [CaseDocument]
        range = [Judge]
    hasJudge.label = ["has Judge"]
    hasJudge.comment = ["Metadata-derived judge linkage from judges, normalized to shared Judge entities and stabilized with judges_id when present."]

    class referencesSecondaryApplication(ObjectProperty):
        domain = [CaseDocument]
        range = [Application]
    referencesSecondaryApplication.label = ["references Secondary Application"]
    referencesSecondaryApplication.comment = ["Metadata-derived cross-reference from secondary_appno linking the case to additional normalized Application entities."]

    class hasLegalStatus(ObjectProperty):
        domain = [Party]
        range = [LegalStatus]
    hasLegalStatus.label = ["has Legal Status"]

    class hasVulnerability(ObjectProperty):
        domain = [Party]
        range = [VulnerabilityStatus]
    hasVulnerability.label = ["has Vulnerability"]

    class citesLaw(ObjectProperty):
        domain = [CaseDocument]
        range = [Law]
    citesLaw.label = ["cites Law"]
    citesLaw.comment = ["Legal-source citation relation aligned to dcterms:references."]

    class hasJudgeCountry(ObjectProperty):
        domain = [Judge]
        range = [Country]
    hasJudgeCountry.label = ["has judge country"]
    hasJudgeCountry.comment = ["Links a judge to country metadata (typically from judge authority records), normalized to Country entities."]

    class hasKeyword(ObjectProperty):
        domain = [CaseDocument]
        range = [Keyword]
    hasKeyword.label = ["has Keyword Label"]
    hasKeyword.comment = ["Metadata-derived normalized thesaurus labels from kpthesaurus_labels for searchable topical indexing."]


# ---------------------------------------------------------------------------
# Controlled Vocabulary Individuals
# ---------------------------------------------------------------------------
CONTROLLED_VOCAB: list[tuple[str, str, str, str | None]] = [
    
    # --- Importance Levels (with OntoCast semantic definitions) ---
    ("Importance_1", "ImportanceLevel", "Key Case", "Judgments that make a significant contribution to the development, clarification or modification of ECtHR case-law."),
    ("Importance_2", "ImportanceLevel", "Important Case", "Judgments that do not make a significant contribution to case-law but nevertheless do not merely apply existing case-law."),
    ("Importance_3", "ImportanceLevel", "Case Report", "Judgments that simply apply existing case-law."),
    ("Importance_4", "ImportanceLevel", "Case of Little Interest", "Judgments of little legal interest, often repetitive."),

    # --- Article 6 Limbs ---
    ("Limb_Civil", "Article6Limb", "Civil", "Article 6 limb indicating civil rights and obligations proceedings."),
    ("Limb_Criminal", "Article6Limb", "Criminal", "Article 6 limb indicating criminal charge proceedings."),
    ("Limb_Mixed", "Article6Limb", "Mixed", "Article 6 limb used when both civil and criminal dimensions are present."),
    ("Limb_Constitutional", "Article6Limb", "Constitutional", "Article 6 limb indicating constitutional court review or constitutional adjudication context."),
    ("Limb_Unspecified", "Article6Limb", "Unspecified", "Article 6 limb used when the source metadata does not clearly distinguish a procedural limb."),

    # --- Chamber Types ---
    ("GrandChamber", "ChamberType", "Grand Chamber", "Highest court level"),
    ("Chamber", "ChamberType", "Chamber", "Court handling most merits cases."),
    ("Committee", "ChamberType", "Committee", "Smaller formation generally used for filtering or repetitive-case handling, also admissibility."),
    ("Commission", "ChamberType", "Commission", "Legacy Commission formation used in pre-Court procedural contexts."),

    # --- Gender ---
    ("Gender_Female", "Gender", "Female", "Gender value indicating female identity."),
    ("Gender_Male", "Gender", "Male", "Gender value indicating male identity."),
    ("Gender_NonBinary", "Gender", "Non-binary", "Gender value indicating non-binary identity."),
    ("Gender_Unknown", "Gender", "Unknown", "Gender value used when gender cannot be inferred from available data."),
    ("Gender_NotStated", "Gender", "Not stated", "Gender value used when the source does not explicitly state gender."),

    # --- Law System ---
    ("LawSystem_Civil", "LawSystem", "Civic code jurisdiction", "Law-system category for primarily civil-law/codified legal traditions."),
    ("LawSystem_Common", "LawSystem", "Common Law jurisdiction", "Law-system category for precedent-driven common-law traditions."),
    ("LawSystem_Mixed", "LawSystem", "Mixed jurisdiction", "Law-system category for jurisdictions combining civil and common-law elements."),

    # --- Separate Opinion Indicators (with OntoCast semantic definitions) ---
    ("SeparateOpinion_Present", "SeparateOpinionIndicator", "Separate Opinion Present", "Indicates that one or more judges appended a concurring or dissenting opinion to the judgment."),
    ("SeparateOpinion_Absent", "SeparateOpinionIndicator", "No Separate Opinion", "Indicates that no separate opinions were appended to the judgment."),

]

# Wikidata Q-nodes for specific individuals
INDIVIDUAL_ALIGNMENTS: dict[str, str] = {
    "Gender_Female": "Q6581072",
    "Gender_Male": "Q6581097",
    "Gender_NonBinary": "Q48270",
    "GrandChamber": "Q108704123",
    "LawSystem_Common": "Q30216",
    "LawSystem_Civil": "Q5950118"
}

def _create_individuals() -> None:
    """Create schema-controlled vocabulary individuals only (no metadata ingestion)."""
    
    # 1. Prepare dummy instances in the Wikidata namespace for alignment
    wd_instances = {}
    with wd_onto:
        for q_id in INDIVIDUAL_ALIGNMENTS.values():
            wd_instances[q_id] = Thing(q_id)

    # 2. Instantiate local individuals
    with onto:
        for vocab_item in CONTROLLED_VOCAB:
            iri_name, class_name, label, description = vocab_item
            cls = getattr(onto, class_name)
            individual = cls(iri_name)
            individual.label = [locstr(label, "en")]
            if description:
                individual.comment = [locstr(description, "en")]
                
            # 3. Assert owl:sameAs if a Wikidata alignment exists
            if iri_name in INDIVIDUAL_ALIGNMENTS:
                wd_dummy = wd_instances[INDIVIDUAL_ALIGNMENTS[iri_name]]
                individual.equivalent_to.append(wd_dummy)

    # Country individual creation is metadata-dependent and should happen in ingestion,
    # not during schema-only build.

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for schema generation."""
    parser = argparse.ArgumentParser(description="Create the full seed schema programmatically with owlready2.")
    parser.add_argument("--seed-ttl", type=Path, default=DEFAULT_SCHEMA_TTL)
    return parser.parse_args()

def build_schema():
    """Build the full schema model in owlready2 from native class definitions.
    
    All entity classes and properties are defined natively at module level.
    This function creates the controlled vocabulary individuals and returns the ontology.
    """
    _create_individuals()
    return onto

def _replace_ontology_subject(graph: Graph, target_subject: URIRef) -> None:
    """Normalize ontology metadata to a single ontology subject IRI."""
    subjects = list(graph.subjects(RDF.type, OWL.Ontology))
    if not subjects:
        graph.add((target_subject, RDF.type, OWL.Ontology))
        return

    for subject in subjects:
        if subject == target_subject:
            continue

        outgoing = list(graph.predicate_objects(subject))
        incoming = list(graph.subject_predicates(subject))
        for predicate, obj in outgoing:
            graph.add((target_subject, predicate, obj))
        for subj, predicate in incoming:
            graph.add((subj, predicate, target_subject))

        graph.remove((subject, None, None))
        graph.remove((None, None, subject))

    graph.add((target_subject, RDF.type, OWL.Ontology))

def _annotate_schema_graph(graph: Graph) -> None:
    """Post-process the serialized ontology graph.
    
    Handles namespace bindings, property alignments to standard vocabularies,
    normalizes the ontology subject IRI, and ensures gYear range fidelity.
    Class alignments and ontology metadata are declared natively in the ontology module.
    Property alignments (rdfs:subPropertyOf) require post-processing as owlready2 
    doesn't support them natively through the Python API.
    """
    ontology_ref = URIRef(ONTOLOGY_IRI)
    _replace_ontology_subject(graph, ontology_ref)

    # Bind Standard Namespaces for Turtle serialization
    graph.bind("echr", Namespace(ONTOLOGY_BASE_IRI))
    graph.bind("wd", Namespace("http://www.wikidata.org/entity/"))
    graph.bind("dcterms", DCTERMS)
    graph.bind("foaf", FOAF)
    graph.bind("prov", PROV)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)

    # Ontology-level metadata (set/override here so they survive the rdfxml roundtrip cleanly)
    graph.set((ontology_ref, RDFS.label, Literal("ECHR Art. 6 Ontology", lang="en")))
    graph.set((ontology_ref, OWL.versionInfo, Literal("3.0")))
    graph.set((ontology_ref, DCTERMS.description, Literal("An ontology for Art.6 Cases heard at the ECHR", lang="en")))
    graph.set((ontology_ref, DCTERMS.license, LICENSE_IRI))
    created = datetime(2026, 5, 20, tzinfo=timezone.utc)
    graph.set((ontology_ref, DCTERMS.created, Literal(created, datatype=XSD.dateTime)))

    # Add property alignments to standard vocabularies (rdfs:subPropertyOf)
    # Note: These must be RDF triples since owlready2 doesn't expose subPropertyOf in Python API
    property_alignment = {
        "hasCaseName": DCTERMS.title,
        "hasJudgmentDate": DCTERMS.date,
        "hasLanguageCode": DCTERMS.language,
        "hasKeyword": DCTERMS.subject,
        "hasCaseTextPath": DCTERMS.source,
        "hasEcli": DCTERMS.isVersionOf,
        "citesCase": DCTERMS.references,
        "citesLaw": DCTERMS.references,
        "hasJudgmentType": DCTERMS.type,
        "hasApplicantName": FOAF.name,
        "hasLegalRepresentative": DCTERMS.contributor,
        "hasRepresentativeName": FOAF.name,
    }

    for local_prop_name, standard_prop_uri in property_alignment.items():
        local_prop_uri = URIRef(f"{ONTOLOGY_BASE_IRI}{local_prop_name}")
        graph.add((local_prop_uri, RDFS.subPropertyOf, standard_prop_uri))
    
    # Fix gYear ranges for temporal properties (post-processing step)
    # owlready2 doesn't natively support custom XML Schema types
    gyear_props = ["hasYear", "hasTenureBeginYear", "hasTenureEndYear", "hasPresidencyBeginYear", "hasPresidencyEndYear"]
    for prop_name in gyear_props:
        prop_ref = URIRef(f"{ONTOLOGY_BASE_IRI}{prop_name}")
        graph.remove((prop_ref, RDFS.range, None))
        graph.add((prop_ref, RDFS.range, XSD.gYear))
        
def save_schema(schema_onto, seed_ttl_path: Path) -> None:
    """Serialize schema and apply graph post-processing before Turtle output."""
    seed_ttl_path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(suffix=".owl", delete=False) as handle:
        temp_schema_path = Path(handle.name)

    try:
        schema_onto.save(file=str(temp_schema_path), format="rdfxml")
        graph = Graph()
        graph.parse(temp_schema_path)
    finally:
        temp_schema_path.unlink(missing_ok=True)

    _annotate_schema_graph(graph)
    graph.serialize(destination=seed_ttl_path, format="turtle")

def main() -> None:
    """Build and write the seed schema artifact."""
    args = parse_args()
    schema_onto = build_schema()
    save_schema(schema_onto, args.seed_ttl)
    print(f"Wrote schema Turtle to {args.seed_ttl}")

if __name__ == "__main__":
    main()
