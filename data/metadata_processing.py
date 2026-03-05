"""
Script to perform data processing and cleaning of Art. 6 data

Last Updated:
05.03.26

Status:
Completed

History:
v1_0 - cleans data and combines with judge data and legal systems 
"""

import argparse
import ast
import json
import re
from pathlib import Path
from difflib import SequenceMatcher
import matplotlib.pyplot as plt
import pandas as pd
import pycountry
from collections import Counter

# -----------------------------
# Setup
# -----------------------------

#chosen due to relevance, data quality and data coverage
DROP_COLUMNS = [
    "applicability",
    "introductiondate",
    "advopstatus",
    "advopidentifier",
    "appnoparts",
    "isplaceholder",
    "application",
    "doctype",
    "languagenumber",
    "kpdateastext",
    "kpdate",
    "documentcollectionid",
    "documentcollectionid2",
    "echrranking",
    "rank",
    "courts",
]

#mappings from HUDOC
ORIGINATING_BODY_MAPPING = {
    "1": "Commission (First Chamber)",
    "2": "Commission (Second Chamber)",
    "3": "Commission (Plenary)",
    "4": "Court (First Section)",
    "5": "Court (Second Section)",
    "6": "Court (Third Section)",
    "7": "Court (Fourth Section)",
    "8": "Court (Grand Chamber)",
    "9": "Court (Chamber)",
    "15": "Court (Plenary)",
    "16": "Court (Screening Panel",
    "17": "Committee of Ministers",
    "21": "Commission",
    "23": "Court (Fifth Section)",
    "25": "Court (First Section Committee)",
    "26": "Court (Second Section Committee)",
    "27": "Court (Third Section Committee)",
    "28": "Court (Fourth Section Committee)",
    "29": "Court (Fifth Section Committee)",
}

#mappings from HUDOC
TYPE_DESCRIPTION_MAPPING = {
    "8": "Decision",
    "9": "Decision (Partial)",
    "10": "Decision (Final)",
    "11": "Judgment (Interpretation)",
    "12": "Judgment (Just Satisfaction)",
    "13": "Judgment (Lack of Jurisdiction)",
    "14": "Judgment (Merits)",
    "15": "Judgment (Merits and Just Satisfaction)",
    "16": "Judgment (Preliminary Objection)",
    "17": "Judgment (Questions of Procedure)",
    "18": "Judgment (Revision)",
    "19": "Judgment (Struck out of the List)",
    "20": "Decision (P9)",
    "21": "Revision",
    "22": "Restoration",
    "26": "Judgment (Article 46 § 4)",
}

#threshold for fuzzy judge name matching
SIMILARITY_THRESHOLD = 0.7

def parse_args():
    parser = argparse.ArgumentParser(
        description="process ECHR metadata."
    )
    parser.add_argument(
        "--input-json",
        default="data/art_6_judgments_metadata.json",
        help="Input JSONL metadata file.",
    )
    parser.add_argument(
        "--output-json",
        default="data/art_6_judgments_metadata_processed.json",
        help="Output processed JSONL file.",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=SIMILARITY_THRESHOLD,
        help="Fuzzy matching threshold for judges (0-1).",
    )
    return parser.parse_args()

# -----------------------------
# Functions
# -----------------------------

def parse_semicolon_list(value, parse_literal_list=False):
    """convert list-like or semicolon-delimited values to a cleaned Python list."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if pd.isna(value):
        return []

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "[]"}:
        return []

    if parse_literal_list:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (ValueError, SyntaxError):
            pass

    return [part.strip() for part in re.split(r"[;]+", text) if part.strip()]

def process_appno_columns(df):
    """
    make appno fields into lists,
    removes appnos for that case i.e. appellants from the extracted and sclappnos to differentiate them
    as being citations or referenced cases
    """
    
    def clean_appnos(text):
        """
        clean the appnos column with different appnos linked to a judgment
        """
        cleaned = re.sub(r'[^0-9/ ;]', ';', text)
        cleaned = cleaned.replace(" ", ";")
        cleaned = re.sub(r';+', ';', cleaned).strip(';')
        return cleaned

    #clean appnos
    df['appno_clean'] = df['appno'].apply(clean_appnos)

    #create list of appnos
    df['appno_clean'] = df['appno_clean'].str.split(';')


    # replace appno with appno_clean and rename it to appno
    df = df.drop("appno", axis=1)
    df = df.rename(columns={"appno_clean": "appno"})

    _appno_list = df["appno"].apply(parse_semicolon_list)
    _extracted_list = df["extractedappno"].apply(parse_semicolon_list)
    _scl_list = df["sclappnos"].apply(parse_semicolon_list)

    _extracted_list = [
        [item for item in extracted if item not in set(appno)]
        for extracted, appno in zip(_extracted_list, _appno_list)
    ]
    _scl_list = [
        [item for item in scl if item not in set(appno)]
        for scl, appno in zip(_scl_list, _appno_list)
    ]

    df["appno"] = _appno_list
    df["extractedappno"] = _extracted_list
    df["sclappnos"] = _scl_list

    df = df.rename(
        columns={
            "appno": "case_appno",
            "extractedappno": "secondary_appno",
            "sclappnos": "cited_appno",
        }
    )

    return df

def map_to_df(code, mapping):
    """maps the json to the df"""
    if pd.isna(code):
        return None
    return mapping.get(str(code).strip(), None)

def apply_code_mapping(df, column_name, mapping):
    """map coded values to labels, print unmatched codes, and replace in place."""
    temp_col = f"{column_name}_mapped"
    df[temp_col] = df[column_name].apply(map_to_df, args=(mapping,))

    unmatched_codes = set(df[column_name].dropna().astype(str).unique()) - set(mapping.keys())
    if unmatched_codes:
        print(f"Unmatched {column_name} codes: {sorted(unmatched_codes)}")
    else:
        print(f"All {column_name} codes matched.")

    df[column_name] = df[temp_col]
    df = df.drop(columns=[temp_col])
    return df

def extract_appellant(case_name):
    """extracts appellant names from English and French case names"""
    if pd.isna(case_name):
        return None

    name = re.sub(r"^(CASE OF|AFFAIRE)\s+", "", case_name, flags=re.IGNORECASE)

    match = re.search(r"(.+?)(?:\s*\(v\.\s*| v\.)", name, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"(.+?)\s+c\.", name, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    if " v. " in name:
        return name.split(" v. ")[0].strip()
    if " c. " in name:
        return name.split(" c. ")[0].strip()

    return name.strip() if name else None

def get_country_names(respondent_value):
    """maps to country names"""
    codes = parse_semicolon_list(respondent_value)
    if not codes:
        return []

    country_names = []
    for code in codes:
        try:
            country_name = pycountry.countries.get(alpha_3=code).name
            country_names.append(country_name)
        except AttributeError:
            country_names.append(code)

    return country_names

def standardize_country(country_name):
    """standardizes country names"""
    if pd.isna(country_name):
        return None

    country_str = str(country_name).strip()
    try:
        return pycountry.countries.search_fuzzy(country_str)[0].name
    except (LookupError, AttributeError):
        return None

def map_list_to_ids_using_cache(jlist, case_match_info, threshold):
    """map a list of case judge names to judge_ids using a precomputed cache.
    """
    out = []
    for j in jlist:
        info = case_match_info.get(j)
        if info and info.get("score", 0.0) >= threshold and info.get("id") is not None:
            out.append(info["id"])
    return out

def map_list_to_scores_using_cache(jlist, case_match_info):
    """return similarity scores for a list of case judge names using precomputed cache."""
    return [case_match_info.get(j, {"score": 0.0})["score"] for j in jlist]

def process_judges(df, judges_csv_path, judges_output_path, unmatched_path, threshold):
    """load judges reference, compute best matches for case judge names,
    populate `judges_id` and `judge_similarity_pct` on `df`, write report
    of unmatched judges, and export processed `judges_df`.
    """
    judges_df = pd.read_csv(judges_csv_path)
    judges_df.insert(0, "judge_id", range(1, len(judges_df) + 1))

    #normalize judge country
    judges_df["country_standardized"] = judges_df["Country"].apply(standardize_country)
    judges_df["Country"] = judges_df["country_standardized"]
    judges_df = judges_df.drop(columns=["country_standardized"])

    #prepare mappings and counts
    judges_df["Judge Name_lower"] = judges_df["Judge Name"].str.lower()
    case_judge_lower_map = dict(zip(judges_df["Judge Name_lower"], judges_df["judge_id"]))

    all_case_judges = [j for sub in df["judges"] for j in sub]
    case_judge_counts = Counter(all_case_judges)
    unique_case_judges = sorted(case_judge_counts.keys())

    #precompute best matches
    case_match_info = {}
    for case_judge in unique_case_judges:
        case_lower = case_judge.lower()
        if case_lower in case_judge_lower_map:
            matched_id = case_judge_lower_map[case_lower]
            best_name = judges_df[judges_df["judge_id"] == matched_id]["Judge Name"].values[0]
            case_match_info[case_judge] = {"id": matched_id, "score": 1.0, "best_match": best_name}
            continue

        best_score = 0.0
        best_id = None
        best_name = None
        for ref_lower, jid in case_judge_lower_map.items():
            score = SequenceMatcher(None, case_lower, ref_lower).ratio()
            if score > best_score:
                best_score = score
                best_id = jid
                best_name = judges_df[judges_df["judge_id"] == jid]["Judge Name"].values[0]

        case_match_info[case_judge] = {"id": best_id, "score": best_score, "best_match": best_name}

    #populate df columns using cache
    df["judges_id"] = df["judges"].apply(lambda jl: map_list_to_ids_using_cache(jl, case_match_info, threshold))
    df["judge_similarity_pct"] = df["judges"].apply(lambda jl: map_list_to_scores_using_cache(jl, case_match_info))

    #build unmapped summary and write to a text file
    unmapped_judges = {
        j: {"count": case_judge_counts[j], "similarity": case_match_info[j]["score"], "best_match": case_match_info[j]["best_match"]}
        for j in unique_case_judges
        if case_match_info[j]["score"] < threshold
    }

    threshold_pct = int(threshold * 100)
    if unmapped_judges:
        with open(unmatched_path, "w", encoding="utf-8") as out_f:
            out_f.write(f"UNMAPPED JUDGES (similarity < {threshold_pct}%) - ({len(unmapped_judges)})\n")
            for judge in sorted(unmapped_judges.keys()):
                info = unmapped_judges[judge]
                out_f.write(f"{judge}\n")
                out_f.write(f"  Best match: {info['best_match']} ({info['similarity'] * 100:.1f}%)\n")
                out_f.write(f"  Found in {info['count']} case(s)\n\n")
        print(f"Wrote unmatched judges report to '{unmatched_path}'")
    else:
        print(f"All judges matched with >={threshold_pct}% similarity!")

    judges_df.to_json(judges_output_path, orient="records", indent=2)
    print(f"Exported judges_df to '{judges_output_path}'")

    return df, judges_df

def map_kpthesaurus(kp_value, key_labels):
    """maps the key words to labels"""
    
    kp_ids = parse_semicolon_list(kp_value)
    if not kp_ids:
        return []

    mapped_labels = []

    for kp_id in kp_ids:
        if kp_id in key_labels:
            mapped_labels.append(key_labels[kp_id])
        else:
            mapped_labels.append(f"[Unmapped: {kp_id}]")

    return mapped_labels

def classify_art6_limb(thesaurus_labels):
    """
    Rreads HUDOC kpthesaurus text and classifies the case into
    Civil, Criminal, Both, or Unspecified.
    """
    if thesaurus_labels is None or (isinstance(thesaurus_labels, float) and pd.isna(thesaurus_labels)):
        return "Unspecified"

    if isinstance(thesaurus_labels, str):
        labels = [thesaurus_labels]
    elif isinstance(thesaurus_labels, list):
        labels = [str(item) for item in thesaurus_labels if str(item).strip()]
    else:
        return "Unspecified"

    normalized = " ".join(labels).lower()

    criminal_keywords = [
        "criminal proceedings",
        "extradition",
        "expulsion",
    ]
    civil_keywords = [
        "civil proceedings",
        "administrative proceedings",
        "disciplinary proceedings",
        "enforcement proceedings",
    ]

    is_criminal = any(keyword in normalized for keyword in criminal_keywords)
    is_civil = any(keyword in normalized for keyword in civil_keywords)

    if is_criminal and is_civil:
        return "Both"
    if is_criminal:
        return "Criminal"
    if is_civil:
        return "Civil"
    
    return "Unspecified"

def get_law_system(country_list, law_system_mapping):
    """map to law system civic vs common law"""
    if not country_list:
        return None

    systems = set()
    for country in country_list:
        system = law_system_mapping.get(country, None)
        if system:
            systems.add(system)

    if len(systems) == 0:
        return None
    if len(systems) == 1:
        return list(systems)[0]
    return "Mixed"

def resolve_path(path_str, cwd, base_dir, output=False):
    """resolve input/output paths using cwd first and script dir fallback for inputs."""
    
    path = Path(path_str)
    if path.is_absolute():
        return path

    cwd_candidate = cwd / path
    if output:
        return cwd_candidate

    base_candidate = base_dir / path
    if cwd_candidate.exists():
        return cwd_candidate
    if base_candidate.exists():
        return base_candidate
    return cwd_candidate

def save_processed_dataset(df, output_json_path):
    """saves data"""
    df.to_json(output_json_path, orient="records", lines=True, date_format="iso")
    print(f"Exported processed dataset to '{output_json_path}'")

# -----------------------------
# Main script
# -----------------------------

def main(args):
    base_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()

    input_json_path = resolve_path(args.input_json, cwd=cwd, base_dir=base_dir)
    output_json_path = resolve_path(args.output_json, cwd=cwd, base_dir=base_dir, output=True)
    judges_csv_path = resolve_path("data/additional_data/judges.csv", cwd=cwd, base_dir=base_dir)
    key_labels_path = resolve_path("data/mappings/key_labels.json", cwd=cwd, base_dir=base_dir)
    law_system_path = resolve_path("data/mappings/law_system_mapping.json", cwd=cwd, base_dir=base_dir)
    judges_output_path = resolve_path("data/additional_data/judges_processed.json", cwd=cwd, base_dir=base_dir, output=True)
    unmatched_path = resolve_path("data/additional_data/unmatched_judges.txt", cwd=cwd, base_dir=base_dir, output=True)
    
    if not 0.0 <= args.similarity_threshold <= 1.0:
        raise ValueError("--similarity-threshold must be between 0 and 1.")

    #load raw data
    df = pd.read_json(input_json_path, lines=True)

    #drop unneeded  columns
    df = df.drop(DROP_COLUMNS, axis=1)

    #normalize and filter appno fields
    df = process_appno_columns(df)

    #ensure importance nums are int
    df["importance"] = pd.to_numeric(df["importance"]).astype("Int64")

    #map originating body
    df = apply_code_mapping(df, "originatingbody", ORIGINATING_BODY_MAPPING)

    #enforce date type
    df["judgementdate"] = pd.to_datetime(df["judgementdate"], dayfirst=True, errors="raise")
    
    #extract appellant names
    df = df.rename(columns={"docname": "case_name"})
    df["appellant"] = df["case_name"].apply(extract_appellant)

    #map type description
    df = apply_code_mapping(df, "typedescription", TYPE_DESCRIPTION_MAPPING)
    df = df.rename(columns={"typedescription": "judgment_type"})

    #map to pycountry country names
    df["country_name"] = df["respondent"].apply(get_country_names)
    df = df.rename(columns={"doctypebranch": "court_level"})

    #judges: parse, match and write report + processed judges reference
    df["judges"] = df["judges"].apply(parse_semicolon_list)
    df, _ = process_judges(df, judges_csv_path, judges_output_path, unmatched_path, args.similarity_threshold)

    #map kpthesaurus labels i.e. legal concept key words
    with open(key_labels_path, "r", encoding="utf-8") as f:
        key_labels = json.load(f)

    df["kpthesaurus_labels"] = df["kpthesaurus"].apply(lambda value: map_kpthesaurus(value, key_labels))

    #check the unmapped key words if any
    unmapped_kp = set()
    for kp_value in df["kpthesaurus"]:
        if pd.notna(kp_value) and kp_value != "":
            kp_ids = parse_semicolon_list(kp_value)
            for kp_id in kp_ids:
                if kp_id not in key_labels:
                    unmapped_kp.add(kp_id)

    print("Mapped kpthesaurus to labels")
    if unmapped_kp:
        print(f"Unmapped kpthesaurus IDs ({len(unmapped_kp)}): {sorted(unmapped_kp)}")
    else:
        print("All kpthesaurus IDs successfully mapped!")

    #convert semicolon-separated fields to lists
    df["article"] = df["article"].apply(parse_semicolon_list)
    df["violation"] = df["violation"].apply(parse_semicolon_list)
    df["nonviolation"] = df["nonviolation"].apply(parse_semicolon_list)
    df["conclusion"] = df["conclusion"].apply(parse_semicolon_list)

    print("Converted article, violation, nonviolation, and conclusion to Python lists")

    #classify between civil and criminal cases
    df["article_6_limb"] = df["kpthesaurus"].apply(classify_art6_limb)
    print("Added article_6_limb classification column")

    #map the legal systems affecting the cases
    with open(law_system_path, "r", encoding="utf-8") as f:
        law_system_mapping = json.load(f)
    df["law_system"] = df["country_name"].apply(lambda c_list: get_law_system(c_list, law_system_mapping))
    print("Mapped legal systems to cases")
    print(df["law_system"].value_counts(dropna=False))

    #save
    save_processed_dataset(df, output_json_path)


if __name__ == "__main__":
    cli_args = parse_args()
    main(cli_args)
