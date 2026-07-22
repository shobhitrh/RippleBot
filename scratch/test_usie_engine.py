import os
import sys
import unittest
import pandas as pd

# Ensure backend modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.src.excel_parser import (
    extract_cell_representations,
    classify_region_component,
    is_crosstab_table,
    unpivot_crosstab_table,
    RegionType,
    SheetCapabilities,
    ICMCell,
    ICMRegion,
    ICMSheet,
    ICMWorkbook,
    compute_semantic_region_hash,
    TOONSerializer,
    MarkdownSerializer,
    JSONSerializer,
)

from backend.src.table_store import InvertedCellIndex, GLOBAL_CELL_INDEX
from backend.src.router.chat import (
    rewrite_query_intent,
    normalize_query_entities,
    fast_path_intent_router,
    verify_retrieval_results,
)

class TestUSIEEngine(unittest.TestCase):

    def test_multi_type_cell_extraction(self):
        """Test multi-type cell representation extraction."""
        c1 = extract_cell_representations("$1,250.50")
        self.assertEqual(c1.numeric_val, 1250.50)
        self.assertEqual(c1.display_val, "$1,250.50")

        c2 = extract_cell_representations("YES")
        self.assertTrue(c2.bool_val)

        c3 = extract_cell_representations("2025-01-30")
        self.assertEqual(c3.date_val, "2025-01-30")

        c4 = extract_cell_representations(None)
        self.assertIsNone(c4.raw_val)
        self.assertEqual(c4.display_val, "")

    def test_icm_data_structures(self):
        """Test ICMWorkbook, ICMSheet, ICMRegion creation."""
        df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
        region = ICMRegion(
            region_id="table_1",
            region_type=RegionType.DATA_TABLE,
            table_title="Users Table",
            df=df,
            headers=["id", "name"],
            bounds=(0, 2, 0, 2),
            confidence=0.95,
            confidence_reason="Detected clear grid with headers"
        )
        sheet = ICMSheet(
            sheet_name="Users",
            regions=[region],
            capabilities=SheetCapabilities(has_tables=True, supports_sql=True)
        )
        wb = ICMWorkbook(filename="test.xlsx", sheets=[sheet], total_rows=2, total_cols=2)

        self.assertEqual(len(wb.sheets), 1)
        self.assertEqual(len(wb.sheets[0].regions), 1)
        self.assertEqual(wb.sheets[0].regions[0].region_type, RegionType.DATA_TABLE)

    def test_semantic_region_hash_stability(self):
        """Test that compute_semantic_region_hash is stable."""
        df = pd.DataFrame({"city": ["Mumbai", "Delhi"], "code": ["MUM", "DEL"]})
        region = ICMRegion(
            region_id="city_tbl",
            region_type=RegionType.DATA_TABLE,
            table_title="City Master",
            df=df,
            headers=["city", "code"],
            bounds=(0, 2, 0, 2)
        )
        hash1 = compute_semantic_region_hash(region)
        hash2 = compute_semantic_region_hash(region)
        self.assertEqual(hash1, hash2)

    def test_serializer_interface(self):
        """Test TOON, Markdown, and JSON serializers."""
        df = pd.DataFrame({"col1": ["a"], "col2": [1]})
        region = ICMRegion(
            region_id="t1",
            region_type=RegionType.DATA_TABLE,
            table_title="Test Table",
            df=df,
            headers=["col1", "col2"],
            bounds=(0, 1, 0, 2)
        )

        toon_str = TOONSerializer().serialize(region)
        self.assertIn('"region_id": "t1"', toon_str)

        md_str = MarkdownSerializer().serialize(region)
        self.assertIn("## Region: t1", md_str)

        json_str = JSONSerializer().serialize(region)
        self.assertIn('"col1":"a"', json_str)

    def test_region_classification(self):
        """Test classify_region_component."""
        df_tbl = pd.DataFrame({"colA": [1, 2, 3], "colB": [4, 5, 6]})
        rtype, conf, _ = classify_region_component(df_tbl, 0, 3, 0, 2)
        self.assertEqual(rtype, RegionType.DATA_TABLE)

        df_kv = pd.DataFrame({"key": ["Status:", "Owner:"], "value": ["Active", "Alice"]})
        rtype_kv, _, _ = classify_region_component(df_kv, 0, 2, 0, 2)
        self.assertEqual(rtype_kv, RegionType.KEY_VALUE)

        df_note = pd.DataFrame({"col1": ["Note: Confidential"]})
        rtype_note, _, _ = classify_region_component(df_note, 0, 1, 0, 1)
        self.assertEqual(rtype_note, RegionType.NOTES)

        df_empty = pd.DataFrame()
        rtype_unk, _, _ = classify_region_component(df_empty, 0, 0, 0, 0)
        self.assertEqual(rtype_unk, RegionType.UNKNOWN)

    def test_crosstab_unpivoting(self):
        """Test 2D crosstab matrix detection and unpivoting."""
        crosstab_raw = pd.DataFrame([
            ["Metric", "2024", "2024", "2025"],
            ["Metric", "Q1", "Q2", "Q1"],
            ["Sales", 100, 150, 200],
            ["Profit", 20, 30, 45]
        ])
        is_cross, num_hdrs = is_crosstab_table(crosstab_raw)
        self.assertTrue(is_cross)
        self.assertEqual(num_hdrs, 2)

        flat_df = unpivot_crosstab_table(crosstab_raw, num_header_rows=2)
        self.assertEqual(len(flat_df), 6)
        self.assertIn("Value", flat_df.columns)

    def test_inverted_cell_index(self):
        """Test InvertedCellIndex exact token lookups."""
        idx = InvertedCellIndex()
        df = pd.DataFrame({"emp_id": ["EMP-101", "EMP-102"], "name": ["Rahul Sharma", "Priya Singh"]})
        idx.build_index(df, "employees_table")

        results = idx.search("Rahul")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0][0], "employees_table")

    def test_query_pipeline_milestone5(self):
        """Test Query Rewriter, Entity Normalizer, Fast-Path Router, and Verification Gate."""
        # 1. Query Rewriter
        q_rewritten = rewrite_query_intent("how many employees are in sales")
        self.assertIn("Count total records", q_rewritten)

        # 2. Fast-Path Router
        schema = {"candidates_table": {"title": "candidates", "columns": [{"name": "id", "type": "INTEGER"}]}}
        fast_res = fast_path_intent_router("how many rows in candidates", schema)
        self.assertIsNotNone(fast_res)
        self.assertIn('SELECT COUNT(*) FROM "candidates_table"', fast_res["sql_query"])

        # 3. Verification Gate (0 rows fallback)
        GLOBAL_CELL_INDEX.build_index(pd.DataFrame({"code": ["HDFC-001"]}), "bank_table")
        empty_res = {"route": "SQL", "formatted_result": "0 rows returned"}
        verified = verify_retrieval_results(empty_res, "HDFC-001")
        self.assertEqual(verified["route"], "CELL_INDEX")
        self.assertIn("HDFC-001", verified["formatted_result"])

if __name__ == "__main__":
    unittest.main()
