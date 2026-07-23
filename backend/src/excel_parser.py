import os
import re
import csv
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import pandas as pd
import openpyxl

logger = logging.getLogger(__name__)

def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())

def get_db_path(company_id: str = None) -> str:
    """
    Per-tenant SQLite database path: <knowledge_base>/db/<company_id>_tables.db.
    Different companies get physically separate .db files so the SQL router can
    never read another tenant's tables. Falls back to the default company.
    """
    from backend.src import config
    cid = config.normalize_company_id(company_id or config.DEFAULT_COMPANY_ID)
    db_dir = os.path.join(config.DOCUMENTS_DIR, "db")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, f"{cid}_tables.db")

def sanitize_name(name: str) -> str:
    """Sanitize filename, sheet name or table ID to be a safe SQL table name."""
    clean = re.sub(r'[^\w]', '_', name)
    clean = re.sub(r'_+', '_', clean)
    clean = clean.lower().strip("_")
    if clean and clean[0].isdigit():
        clean = "t_" + clean
    return clean

def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert columns to numeric where possible, keeping non-numeric columns as-is.
    Replaces df.apply(pd.to_numeric, errors='ignore'): 'ignore' was removed in
    pandas 3.x and raises "ValueError: invalid error value specified" — which
    broke indexing on fresh installs (e.g. Railway) while older local pandas
    only warned. This per-column try/except works on every pandas version.
    """
    def _maybe_numeric(col):
        try:
            return pd.to_numeric(col)
        except (ValueError, TypeError):
            return col
    return df.apply(_maybe_numeric)


def to_json_serializable(val):
    """Convert numpy or pandas types to JSON-serializable native Python types."""
    import datetime
    if pd.isna(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if hasattr(val, "item"):
        try:
            return val.item()
        except Exception:
            pass
    return val

# ── Internal Canonical Model (ICM) Dataclasses & Helpers ──
from dataclasses import dataclass, field
from enum import Enum

class RegionType(str, Enum):
    DATA_TABLE = "data_table"
    KEY_VALUE = "key_value"
    NOTES = "notes"
    DASHBOARD = "dashboard"
    UNKNOWN = "unknown"

@dataclass
class ICMCell:
    raw_val: Optional[object] = None
    display_val: str = ""
    numeric_val: Optional[float] = None
    date_val: Optional[str] = None
    bool_val: Optional[bool] = None

@dataclass
class SheetCapabilities:
    has_tables: bool = True
    supports_sql: bool = True
    supports_cell_search: bool = True
    has_notes: bool = False

@dataclass
class ICMRegion:
    region_id: str
    region_type: RegionType
    table_title: str
    df: pd.DataFrame
    headers: List[str]
    bounds: Tuple[int, int, int, int]  # (min_r, max_r, min_c, max_c)
    confidence: float = 1.0
    confidence_reason: str = "Standard grid component"
    metadata: Dict = field(default_factory=dict)

@dataclass
class ICMSheet:
    sheet_name: str
    regions: List[ICMRegion] = field(default_factory=list)
    capabilities: SheetCapabilities = field(default_factory=SheetCapabilities)

@dataclass
class ICMWorkbook:
    filename: str
    sheets: List[ICMSheet] = field(default_factory=list)
    total_rows: int = 0
    total_cols: int = 0

def extract_cell_representations(val: object) -> ICMCell:
    """Extract multi-type cell representation (raw, display, numeric, date, bool)."""
    if val is None or pd.isna(val):
        return ICMCell(raw_val=None, display_val="", numeric_val=None, date_val=None, bool_val=None)
    
    raw_val = val
    display_val = str(val).strip()
    
    # 1. Check boolean
    bool_val = None
    if isinstance(val, bool):
        bool_val = val
    elif display_val.lower() in ("true", "yes", "y"):
        bool_val = True
    elif display_val.lower() in ("false", "no", "n"):
        bool_val = False

    # 2. Check numeric
    numeric_val = None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        numeric_val = float(val)
    else:
        # Clean currency/comma strings like "$1,250.50" or " 1250 "
        clean_num_str = re.sub(r'[^\d.-]', '', display_val)
        if clean_num_str and clean_num_str not in ("-", ".", "-."):
            try:
                numeric_val = float(clean_num_str)
            except ValueError:
                numeric_val = None

    # 3. Check date
    date_val = None
    if isinstance(val, (datetime, pd.Timestamp)):
        date_val = val.isoformat()
    elif isinstance(val, str):
        # Try simple ISO or standard date formats
        if re.match(r'^\d{4}-\d{2}-\d{2}', display_val):
            date_val = display_val

    return ICMCell(
        raw_val=raw_val,
        display_val=display_val,
        numeric_val=numeric_val,
        date_val=date_val,
        bool_val=bool_val
    )

import hashlib

def compute_semantic_region_hash(region: ICMRegion) -> str:
    """
    Compute a stable MD5 hash based strictly on semantic content
    (RegionType + Headers + Shape + Coordinates + Sample values).
    Serializer formatting changes will NOT alter this hash.
    """
    hasher = hashlib.md5()
    hasher.update(str(region.region_type.value).encode('utf-8'))
    hasher.update(",".join(region.headers).encode('utf-8'))
    hasher.update(str(region.bounds).encode('utf-8'))
    
    if not region.df.empty:
        shape_str = f"{len(region.df)}x{len(region.df.columns)}"
        hasher.update(shape_str.encode('utf-8'))
        sample_vals = str(region.df.head(10).to_dict(orient='records'))
        hasher.update(sample_vals.encode('utf-8'))
        
    return hasher.hexdigest()

class BaseSerializer:
    """Abstract Serializer interface for converting ICMRegion to text/metadata."""
    def serialize(self, region: ICMRegion) -> str:
        raise NotImplementedError

class TOONSerializer(BaseSerializer):
    """TOON (Object-Oriented Normalized JSON) Serializer."""
    def serialize(self, region: ICMRegion) -> str:
        data_dict = {
            "region_id": region.region_id,
            "type": region.region_type.value,
            "title": region.table_title,
            "headers": region.headers,
            "rows_count": len(region.df),
            "bounds": list(region.bounds),
            "confidence": region.confidence,
            "confidence_reason": region.confidence_reason,
            "preview_rows": region.df.head(5).to_dict(orient="records")
        }
        return json.dumps(data_dict, indent=2, default=to_json_serializable)

class MarkdownSerializer(BaseSerializer):
    """Markdown Serializer for vector embeddings & prompt context."""
    def serialize(self, region: ICMRegion) -> str:
        title_str = f" (Title: {region.table_title})" if region.table_title else ""
        preview_md = region.df.head(5).fillna("").to_markdown(index=False)
        return (
            f"## Region: {region.region_id}{title_str} [{region.region_type.value}]\n"
            f"Total Rows: {len(region.df)} | Columns ({len(region.headers)}): {', '.join(region.headers)}\n\n"
            f"### Sample Preview:\n{preview_md}"
        )

class JSONSerializer(BaseSerializer):
    """Full JSON Serializer."""
    def serialize(self, region: ICMRegion) -> str:
        return region.df.to_json(orient="records", default_handler=to_json_serializable)

def detect_csv_properties(file_path: str) -> Tuple[str, str]:
    """Sniff CSV encoding and delimiter defensively."""
    # 1. Detect Encoding
    encodings = ["utf-8", "latin-1", "utf-16", "cp1252"]
    detected_encoding = "utf-8"
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                f.read(2048)
            detected_encoding = enc
            break
        except Exception:
            continue
            
    # 2. Sniff Delimiter
    detected_delimiter = ","
    try:
        with open(file_path, "r", encoding=detected_encoding, errors="ignore") as f:
            sample = f.read(4096)
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample)
            if dialect.delimiter in (",", ";", "\t", "|"):
                detected_delimiter = dialect.delimiter
    except Exception:
        # Fall back to checking common delimiters in sample
        try:
            with open(file_path, "r", encoding=detected_encoding, errors="ignore") as f:
                sample = f.read(2048)
                counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
                best = max(counts, key=counts.get)
                if counts[best] > 0:
                    detected_delimiter = best
        except Exception:
            pass
            
    return detected_encoding, detected_delimiter

def process_csv_file(file_path: str) -> Tuple[List[Dict], List[Tuple[str, pd.DataFrame, str]]]:
    """
    Parse a CSV file using UnifiedExcelRAGPipeline.
    Generates Unified Markdown, .md.gz archive, and context-injected RAG chunks.
    Returns (chunks, sqlite_tables).
    """
    from backend.src.unified_excel_pipeline import UnifiedExcelRAGPipeline
    pipeline = UnifiedExcelRAGPipeline()
    return pipeline.process_file(file_path)

def is_key_value_block(df: pd.DataFrame) -> bool:
    """Detect if a dataframe is structured as a key-value block (e.g. metadata list)."""
    non_empty_cols = [col for col in df.columns if not df[col].isna().all()]
    if len(non_empty_cols) != 2:
        return False
    col0_vals = df[non_empty_cols[0]].dropna().astype(str).str.strip()
    if len(col0_vals) < 2:
        return False
    
    colon_ends = sum(1 for v in col0_vals if v.endswith(":") or v.lower() in (
        "account", "sales team", "customer success manager", "client spoc", "scope",
        "contract signed on", "contract tenure", "renewal date", "launch date",
        "contract value", "license", "database", "implementation", "integration",
        "support", "success fee", "sms cost", "customization"
    ))
    ratio = colon_ends / len(col0_vals)
    return ratio >= 0.3

def detect_data_start_row(sheet_val, min_r, max_r, filtered_cols_indices, grid_val) -> int:
    """
    Scans row by row from top of the component to detect where table header ends and data begins.
    Uses cell styling (bold/fills) and presence of numeric/formula values as signals.
    """
    if len(filtered_cols_indices) <= 2:
        return 1
        
    num_rows = max_r - min_r + 1
    if num_rows <= 1:
        return 0
        
    for idx, r_idx in enumerate(range(min_r, max_r + 1)):
        row_vals = [grid_val[r_idx][c_idx] for c_idx in filtered_cols_indices]
        non_empty_vals = [v for v in row_vals if v is not None and str(v).strip() != ""]
        if not non_empty_vals:
            continue
            
        # 1. Check for numeric values in row (excluding common year numbers)
        has_numeric = False
        for v in non_empty_vals:
            if isinstance(v, (int, float)):
                if v > 2100 or v < 0 or (v > 20 and v != 2024 and v != 2025 and v != 2026 and v != 2027):
                    has_numeric = True
                    break
            elif isinstance(v, str):
                clean_v = v.replace(",", "").replace("$", "").replace("%", "").strip()
                if clean_v.isdigit():
                    val_int = int(clean_v)
                    if val_int > 2100 or val_int < 0 or (val_int > 20 and val_int != 2024 and val_int != 2025 and val_int != 2026 and val_int != 2027):
                        has_numeric = True
                        break
                else:
                    try:
                        float(clean_v)
                        has_numeric = True
                        break
                    except ValueError:
                        pass
        
        if has_numeric:
            return idx
            
        # 2. Check styling (if a row has low bold/highlight ratio, it is data)
        bold_count = 0
        for c_idx in filtered_cols_indices:
            cell = sheet_val.cell(row=r_idx + 1, column=c_idx + 1)
            is_bold = False
            if cell.font and cell.font.bold:
                is_bold = True
            elif cell.fill and cell.fill.start_color and cell.fill.start_color.rgb not in ("00000000", "000000", "FFFFFFFF", "FFFFFFF"):
                is_bold = True
            if is_bold:
                bold_count += 1
                
        bold_ratio = bold_count / len(non_empty_vals) if non_empty_vals else 0
        if bold_ratio < 0.3:
            return idx
            
    return 1

def is_crosstab_table(df: pd.DataFrame) -> Tuple[bool, int]:
    """
    Check if a DataFrame is a 2D crosstab matrix with multi-row headers.
    Returns (is_crosstab, num_header_rows).
    """
    if len(df) < 3 or len(df.columns) < 3:
        return False, 0

    # Inspect top 2 rows: check if row 0 contains repeated category headers (e.g. Years)
    row0_vals = [str(x).strip() for x in df.iloc[0].values if pd.notna(x) and str(x).strip() != ""]
    if len(row0_vals) >= 2:
        unique_row0 = set(row0_vals)
        if len(unique_row0) < len(row0_vals):
            return True, 2
            
    return False, 0

def unpivot_crosstab_table(df: pd.DataFrame, num_header_rows: int = 2) -> pd.DataFrame:
    """
    Unpivot a 2D matrix report with multi-row headers into a flat relational DataFrame.
    Example:
         Row Metric | 2024 Q1 | 2024 Q2 | 2025 Q1
         Sales      | 100     | 150     | 200
    Becomes:
         Metric | Dimension_1 | Dimension_2 | Value
         Sales  | 2024        | Q1          | 100
    """
    if df.empty or len(df) <= num_header_rows:
        return df

    try:
        metric_col_name = str(df.columns[0]).strip() or "Metric"
        header_levels = []
        for h_idx in range(num_header_rows):
            level_vals = [str(val).strip() if pd.notna(val) else "" for val in df.iloc[h_idx].values]
            header_levels.append(level_vals)

        data_df = df.iloc[num_header_rows:].copy()
        unpivoted_rows = []

        for _, row in data_df.iterrows():
            metric_val = row.iloc[0]
            if pd.isna(metric_val) or str(metric_val).strip() == "":
                continue
            
            for col_idx in range(1, len(df.columns)):
                cell_val = row.iloc[col_idx]
                if pd.isna(cell_val) or str(cell_val).strip() == "":
                    continue
                
                dim_1 = header_levels[0][col_idx] if len(header_levels) > 0 else ""
                dim_2 = header_levels[1][col_idx] if len(header_levels) > 1 else ""
                
                unpivoted_rows.append([str(metric_val).strip(), dim_1, dim_2, cell_val])

        cols = [metric_col_name, "Dimension_1", "Dimension_2", "Value"]
        flat_df = pd.DataFrame(unpivoted_rows, columns=cols)
        return coerce_numeric_columns(flat_df)
    except Exception as e:
        logger.warning(f"unpivot_crosstab_table failed: {e}")
        return df

def classify_region_component(df: pd.DataFrame, min_r: int, max_r: int, min_c: int, max_c: int) -> Tuple[RegionType, float, str]:
    """
    Classify a grid component into RegionType with confidence score and reasoning.
    """
    if df.empty:
        return RegionType.UNKNOWN, 0.0, "Empty region"
        
    nrows, ncols = len(df), len(df.columns)
    
    # 1. Key-Value block
    if is_key_value_block(df):
        return RegionType.KEY_VALUE, 0.95, "2-column key-value layout"
        
    # 2. Regular Data Table (2+ rows, 1+ cols with decent density)
    if nrows >= 2 and ncols >= 1:
        non_empty_ratio = (df.notna().sum().sum()) / (nrows * ncols)
        if non_empty_ratio >= 0.3:
            return RegionType.DATA_TABLE, 0.95, "Structured tabular grid with data"
            
    # 3. Footnote / Notes (single-column short text block or small footnote)
    if nrows <= 3 and ncols == 1:
        return RegionType.NOTES, 0.90, "Short text footnote block"
            
    # 4. Unknown Region fallback
    return RegionType.UNKNOWN, 0.50, "Unstructured grid block preserved as raw region"

PARSER_VERSION = "v6"

def is_same_column_signature(cols_a: List[str], cols_b: List[str]) -> bool:
    """Check if two column sets have >= 90% overlap or matching column count."""
    if not cols_a or not cols_b:
        return False
    if len(cols_a) == len(cols_b):
        # If same length, check match ratio
        matches = sum(1 for a, b in zip(cols_a, cols_b) if a == b or a in cols_b)
        return (matches / len(cols_a)) >= 0.8
    set_a, set_b = set(cols_a), set(cols_b)
    intersection = set_a.intersection(set_b)
    union = set_a.union(set_b)
    if not union:
        return False
    return (len(intersection) / len(union)) >= 0.8

def process_excel_file(file_path: str) -> Tuple[List[Dict], List[Tuple[str, pd.DataFrame, str]]]:
    """
    Parse an Excel (.xlsx, .xls) workbook using UnifiedExcelRAGPipeline.
    Generates Unified Markdown, .md.gz archive, and context-injected RAG chunks.
    Returns (chunks, sqlite_tables).
    """
    from backend.src.unified_excel_pipeline import UnifiedExcelRAGPipeline
    pipeline = UnifiedExcelRAGPipeline()
    return pipeline.process_file(file_path)

def load_tables_to_sqlite(sqlite_tables: List[Tuple[str, pd.DataFrame, str]], company_id: str = None):
    """
    Persist parsed Tier-C tables for this tenant. Legacy name kept for the engine
    call sites, but it now dispatches (via table_store) to Postgres when
    VECTOR_BACKEND=pgvector, or SQLite for local dev. See backend/src/table_store.py.
    """
    from backend.src import table_store
    table_store.load_tables(sqlite_tables, company_id)

def delete_tables_from_sqlite(filename: str, company_id: str = None):
    """Delete all Tier-C tables for a filename (Postgres or SQLite; see table_store)."""
    from backend.src import table_store
    table_store.delete_tables_for_file(filename, company_id)
