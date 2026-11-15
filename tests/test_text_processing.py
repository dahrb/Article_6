from art6.data.text_processing import process_echr_document

HTML = """
<p><b>THE FACTS</b></p>
<p>1. The applicant brought proceedings on 3 May 1990.</p>
<p><b>{law}</b></p>
<p>2. The Court considers that the period began on 3 May 1990.</p>
"""


def test_as_to_the_law_ends_the_facts():
    for heading in ("THE LAW", "AS TO THE LAW"):
        chunks = process_echr_document(HTML.format(law=heading))["text_chunks"]
        assert "3 May 1990" in chunks["facts"]
        assert "The Court considers" not in chunks["facts"], heading
        assert "The Court considers" in chunks["law"], heading
