import os
import sys
import time
import unittest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.src.table_store import InvertedCellIndex
from backend.src.router.entity_extractor import extract_query_entities

class TestEntityExtractionParity(unittest.TestCase):
    def setUp(self):
        self.index = InvertedCellIndex()
        self.sample_df = pd.DataFrame([
            {
                "Business Unit": "Operations",
                "Job Id": 60593501,
                "Job Name": "IRM-Head Wholesale Policy-CB",
                "Certification": "Risk Certification",
                "Qualification": "Graduate",
                "Status": "Active"
            },
            {
                "Business Unit": "Operations",
                "Job Id": 60593502,
                "Job Name": "Regional Ops Mgr-RAO",
                "Certification": "Ops Certification",
                "Qualification": "Post Graduate",
                "Status": "Active"
            }
        ])
        self.index.build_index(self.sample_df, "ispl_certificate_mapping_table_1")

    def test_phrasing_and_synonym_immunity(self):
        test_queries = [
            "Job ID 60593501",
            "what can you tell me about job id 60593501?",
            "run a sanity check for 60593501",
            "give me a rundown for 60593501",
            "fetch details for 60593501 please",
            "60593501"
        ]

        print("\n--- TESTING USIE v4 ENTITY EXTRACTION PARITY ---")
        first_md = None
        for q in test_queries:
            t0 = time.time()
            entities = extract_query_entities(q)
            md = self.index.search_markdown_entities(entities)
            elapsed_ms = (time.time() - t0) * 1000

            print(f"\nQuery: '{q}'")
            print(f"Extracted Entities: {entities} (Latency: {elapsed_ms:.2f}ms)")
            print(f"Markdown Output preview: {md.splitlines()[0] if md else 'NONE'}")

            self.assertIn("60593501", md, f"Failed to retrieve 60593501 for query: {q}")
            self.assertIn("IRM-Head Wholesale Policy-CB", md, f"Failed to retrieve Job Name for query: {q}")
            
            if first_md is None:
                first_md = md
            else:
                self.assertEqual(first_md, md, f"Output mismatch for query: {q}")

        print("\nSUCCESS: All 6 query phrasings produced 100% IDENTICAL Markdown table outputs!")

if __name__ == "__main__":
    unittest.main()
