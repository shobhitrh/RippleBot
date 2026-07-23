import os
import sys
import time
import unittest
import pandas as pd

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.src.table_store import InvertedCellIndex

class TestUSIEv3MultiEngine(unittest.TestCase):
    def setUp(self):
        self.index = InvertedCellIndex()
        # Sample Certificate Mapping DataFrame
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
            },
            {
                "Business Unit": "Finance",
                "Job Id": 70881900,
                "Job Name": "Senior Financial Analyst",
                "Certification": "CPA",
                "Qualification": "Master of Finance",
                "Status": "Active"
            }
        ])
        self.table_name = "ispl_certificate_mapping_table_1"
        self.index.build_index(self.sample_df, self.table_name)

    def test_exact_job_id_lookup(self):
        t0 = time.time()
        md_hit = self.index.search_markdown("What can you tell me about Job ID 60593501?")
        elapsed_ms = (time.time() - t0) * 1000
        
        print(f"\n[Test 1: Exact Job ID Lookup] Latency: {elapsed_ms:.2f}ms")
        print(f"Markdown Output:\n{md_hit}")

        self.assertIn("60593501", md_hit)
        self.assertIn("IRM-Head Wholesale Policy-CB", md_hit)
        self.assertIn("Risk Certification", md_hit)
        self.assertLess(elapsed_ms, 50, "Cell index search must be sub-50ms")

    def test_job_name_keyword_lookup(self):
        t0 = time.time()
        md_hit = self.index.search_markdown("Regional Ops Mgr-RAO")
        elapsed_ms = (time.time() - t0) * 1000

        print(f"\n[Test 2: Job Name Keyword Lookup] Latency: {elapsed_ms:.2f}ms")
        print(f"Markdown Output:\n{md_hit}")

        self.assertIn("60593502", md_hit)
        self.assertIn("Regional Ops Mgr-RAO", md_hit)
        self.assertLess(elapsed_ms, 50)

if __name__ == "__main__":
    unittest.main()
