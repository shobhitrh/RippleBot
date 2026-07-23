import os
import sys
import time
import unittest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.src.table_store import InvertedCellIndex

class TestQueryParity(unittest.TestCase):
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

    def test_short_vs_long_query_parity(self):
        md1 = self.index.search_markdown("Job ID 60593501")
        md2 = self.index.search_markdown("what can you tell me about job id 60593501")

        print(f"\n[Short Query Result]:\n{md1}")
        print(f"\n[Long Query Result]:\n{md2}")

        self.assertIn("60593501", md1)
        self.assertIn("60593501", md2)
        self.assertIn("IRM-Head Wholesale Policy-CB", md1)
        self.assertIn("IRM-Head Wholesale Policy-CB", md2)

if __name__ == "__main__":
    unittest.main()
