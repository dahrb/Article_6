"""
Script to process ECHR decision/judgment HTML into structured JSONL corpus.

Last Updated:
21.04.26

Status:
Done

History:
v1_0 - created reproducible segmented text extraction pipeline 
"""

import argparse
import json
import re
from io import StringIO
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup


def parse_args():
	parser = argparse.ArgumentParser(
		description="Process ECHR HTML corpus into structured JSONL."
	)
	parser.add_argument(
		"--corpus",
		choices=["judgments", "decisions"],
		required=True,
		help="Corpus type to process.",
	)
	parser.add_argument(
		"--input-dir",
		default=None,
		help="Input folder with HTML files. If omitted, defaults by corpus.",
	)
	parser.add_argument(
		"--output-jsonl",
		default=None,
		help="Output JSONL path. If omitted, defaults by corpus.",
	)
	parser.add_argument(
		"--skip-empty-text",
		action="store_true",
		help="Skip cases where extracted full_text is empty.",
	)
	parser.add_argument(
		"--limit",
		type=int,
		default=None,
		help="Optional max number of HTML files to process.",
	)
	return parser.parse_args()


def resolve_defaults(args):
	if args.corpus == "judgments":
		input_dir = args.input_dir or "data/judgment_text"
		output_jsonl = args.output_jsonl or "data/processed_json/echr_corpus.jsonl"
		skip_empty_text = args.skip_empty_text
	else:
		input_dir = args.input_dir or "data/decision_text"
		output_jsonl = args.output_jsonl or "data/processed_json/echr_decisions_corpus.jsonl"
		# Decisions include many historical conversion shells; skip by default for cleaner corpus.
		skip_empty_text = True if not args.skip_empty_text else True

	return {
		"input_dir": Path(input_dir),
		"output_jsonl": Path(output_jsonl),
		"skip_empty_text": skip_empty_text,
	}


def iter_case_soups(input_folder, pattern="*.html", limit=None):
	"""Yield (html_path, BeautifulSoup) pairs from the input folder."""
	folder = Path(input_folder)
	if not folder.exists() or not folder.is_dir():
		raise FileNotFoundError(f"Input folder not found or not a directory: {folder}")

	files = sorted(folder.glob(pattern))
	if limit is not None:
		files = files[:limit]

	for html_path in files:
		html = html_path.read_text(encoding="utf-8", errors="ignore")
		soup = BeautifulSoup(html, "html.parser")
		yield html_path, soup


def process_echr_document(raw_html, is_decision=False):
	"""Extract tables, text markdown, and structured section chunks from one ECHR HTML."""
	extracted_tables = []
	try:
		dfs = pd.read_html(StringIO(raw_html), flavor="lxml")
		for i, df in enumerate(dfs):
			df = df.dropna(how="all", axis=0).dropna(how="all", axis=1)
			if not df.empty:
				extracted_tables.append({f"table_{i + 1}": df.to_dict(orient="records")})
	except ValueError:
		pass

	soup = BeautifulSoup(raw_html, "html.parser")
	for tag in soup(["style", "script", "meta", "link", "table"]):
		tag.decompose()

	block_tags = {"p", "h1", "h2", "h3", "h4", "div"}
	markdown_lines = []

	for element in soup.find_all(["p", "h1", "h2", "h3", "div"]):
		if element.name == "div" and element.find(block_tags):
			continue

		text = element.get_text(separator=" ", strip=True)
		if not text:
			continue

		text = re.sub(r"\s+", " ", text)

		is_bold = element.find("b") is not None or element.find("strong") is not None
		is_all_caps = text.isupper() and len(text) > 4

		if is_all_caps and is_bold and len(text) < 100:
			markdown_lines.append(f"\n# {text}\n")
		elif (is_bold and len(text) < 150) or (is_all_caps and len(text) < 150):
			markdown_lines.append(f"\n## {text}\n")
		else:
			match = re.match(r"^(\d+\.)\s*(.*)", text)
			if match:
				markdown_lines.append(f"**{match.group(1)}** {match.group(2)}")
			else:
				markdown_lines.append(text)

	full_markdown = "\n".join(markdown_lines)

	chunks = {
		"introduction": "",
		"procedure": "",
		"facts": "",
		"legal_framework": "",
		"law": "",
		"reasons": "",
		"appendix": "",
	}

	current_section = "introduction"
	header_pattern = re.compile(
		r"^(?:#+\s*)?(?:[A-Z0-9IVX]+\.\s*)?"
		r"(PROC[EÉ]DURE|"
		r"FACTS\s+AND\s+PROC[EÉ]DURE(?:\s+.*)?|"
		r"AS\s+TO\s+THE\s+FACTS(?:\s+.*)?|"
		r"(?:THE\s+)?FACTS(?:\s*\[\d+\])?(?:\s+.*)?|EN\s+FAIT(?:\s*\[\d+\])?(?:\s+.*)?|LES\s+FAITS(?:\s*\[\d+\])?(?:\s+.*)?|"
		r"(?:THE\s+)?PARTICULAR\s+CIRCUMSTANCES\s+OF\s+THE\s+CASES?(?:\s+.*)?|"
		r"THE\s+CIRCUMSTANCES\s+OF\s+THE\s+CASES?(?:\s+.*)?|CIRCONSTANCES\s+DE\s+L\'AFFAIRE(?:\s+.*)?|"
		r"THE\s+LAW|EN\s+DROIT|"
		r"FOR\s+THESE\s+REAS\s*O\s*NS.*|PAR\s+CES\s+MOTIFS.*|F\s*O\s*R\s+T\s*H\s*E\s*S\s*E\s+R\s*E\s*A\s*S\s*O\s*N\s*S.*|"
		r"NOW\s+THEREFORE\s+THE\s+COMMISSION(?:\s+.*)?|"
		r"(?:D[ÉE]CIDES?|DECLARES?)\s+(?:TO\s+)?(?:STRIKE|ADJOURN|HOLD|REJECT).*|"
		r"(?:UPDATED\s+)?SUBJECT\s+MATTER\s+OF\s+THE\s+CASES?|OBJET\s+DE\s+L\'AFFAIRE|"
		r"STATEMEN[TR]\s+OF\s+FACTS|EXPOS[EÉ]\s+DES\s+FAITS|"
		r"RELEVANT\s+(?:DOMESTIC|INTERNATIONAL|LEGAL)\s+(?:LAW|FRAMEWORK|MATERIAL).*|"
		r"(?:LE\s+)?DROIT\s+(?:ET\s+LA\s+PRATIQUE\s+)?(?:INTERNES?|INTERNATIONAL|PERTINENTS?).*|CADRE\s+JURIDIQUE.*|"
		r"QUESTIONS?\s+(?:TO\s+THE|AUX)\s+PARTIES.*|"
		r"APPENDIX|ANNEXE)\s*$",
		re.IGNORECASE,
	)

	for line in full_markdown.split("\n"):
		clean_line = line.strip()
		match = header_pattern.match(clean_line)

		if match:
			header_text = match.group(1).upper()
			header_text_norm = re.sub(r"\s+", "", header_text)

			if "FACTSANDPROC" in header_text_norm:
				current_section = "facts"
			elif "PROC" in header_text_norm:
				current_section = "facts" if is_decision else "procedure"
			elif any(
				x in header_text_norm
				for x in [
					"FACTS",
					"SUBJECTMATTER",
					"CIRCUMSTANCES",
					"STATEMEN",
					"FAIT",
					"OBJETDE",
					"CIRCONSTANCES",
					"EXPOS",
				]
			):
				current_section = "facts"
			elif any(
				x in header_text_norm
				for x in ["RELEVANT", "PERTINENT", "CADRE", "DROITINTERNE", "DROITINTERNATIONAL"]
			):
				current_section = "legal_framework"
			elif "LAW" in header_text_norm or "ENDROIT" in header_text_norm:
				current_section = "law"
			elif any(
				x in header_text_norm
				for x in ["REASONS", "MOTIFS", "NOWTHEREFORETHECOMMISSION", "DECIDES", "DECLARES"]
			):
				current_section = "reasons"
			elif "APPENDIX" in header_text_norm or "ANNEXE" in header_text_norm:
				current_section = "appendix"
			elif "QUESTION" in header_text_norm:
				current_section = "skip"

			if current_section != "skip":
				chunks[current_section] += f"\n\n{clean_line}\n\n"
			continue

		if current_section != "skip":
			chunks[current_section] += f"{clean_line}\n"

	for key in chunks:
		chunks[key] = chunks[key].strip()

	return {
		"full_text": full_markdown,
		"text_chunks": chunks,
		"tables": extracted_tables,
	}


def process_corpus(input_dir, output_jsonl, is_decision, skip_empty_text=False, limit=None):
	"""Run end-to-end extraction for one corpus and write deterministic JSONL output."""
	output_jsonl.parent.mkdir(parents=True, exist_ok=True)

	success_count = 0
	skipped_empty_count = 0
	error_count = 0

	print(f"Starting extraction: writing fresh corpus to {output_jsonl}...")
	with open(output_jsonl, "w", encoding="utf-8") as outfile:
		for html_path, soup in iter_case_soups(input_folder=input_dir, limit=limit):
			try:
				raw_html = str(soup)
				clean_data = process_echr_document(raw_html, is_decision=is_decision)

				if skip_empty_text and not (clean_data.get("full_text") or "").strip():
					skipped_empty_count += 1
					continue

				itemid = html_path.stem
				clean_data["itemid"] = itemid
				outfile.write(json.dumps(clean_data, ensure_ascii=False) + "\n")
				success_count += 1

				if success_count % 500 == 0:
					print(f"Successfully processed {success_count} cases...")
			except Exception as exc:
				print(f"Error processing {html_path}: {exc}")
				error_count += 1

	print("\nExtraction complete!")
	print(f"Processed cases: {success_count}")
	print(f"Skipped empty text: {skipped_empty_count}")
	print(f"Errors: {error_count}")


def main():
	args = parse_args()
	cfg = resolve_defaults(args)

	print("Running case text processing with configuration:")
	print(f"  corpus: {args.corpus}")
	print(f"  input_dir: {cfg['input_dir']}")
	print(f"  output_jsonl: {cfg['output_jsonl']}")
	print(f"  skip_empty_text: {cfg['skip_empty_text']}")
	print(f"  limit: {args.limit}")

	process_corpus(
		input_dir=cfg["input_dir"],
		output_jsonl=cfg["output_jsonl"],
		is_decision=(args.corpus == "decisions"),
		skip_empty_text=cfg["skip_empty_text"],
		limit=args.limit,
	)

if __name__ == "__main__":
	main()
