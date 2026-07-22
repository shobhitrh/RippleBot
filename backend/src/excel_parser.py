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
    Parse a CSV file as a single clean table.
    Generates Tier A (row JSON) and Tier B (markdown window) chunks.
    Also returns the table to be loaded into SQLite (Tier C).
    """
    filename = os.path.basename(file_path)
    logger.info(f"Ingesting CSV file: {filename}")
    
    encoding, delimiter = detect_csv_properties(file_path)
    try:
        df = pd.read_csv(file_path, encoding=encoding, sep=delimiter, on_bad_lines="skip")
    except Exception as e:
        logger.error(f"pd.read_csv failed for {filename}: {e}")
        # Fallback to python csv reader if pandas parser breaks
        rows = []
        with open(file_path, "r", encoding=encoding, errors="ignore") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                rows.append(row)
        if not rows:
            return [], []
        headers = [h.strip() or f"col_{i}" for i, h in enumerate(rows[0])]
        df = pd.DataFrame(rows[1:], columns=headers)

    if df.empty:
        return [], []

    # Clean headers
    cleaned_headers = []
    seen = {}
    for i, h in enumerate(df.columns):
        h_clean = sanitize_name(str(h))
        if not h_clean or "unnamed" in h_clean.lower():
            h_clean = f"col_{i}"
        if h_clean in seen:
            seen[h_clean] += 1
            cleaned_headers.append(f"{h_clean}_{seen[h_clean]}")
        else:
            seen[h_clean] = 1
            cleaned_headers.append(h_clean)
    df.columns = cleaned_headers

    # Infer real types for SQLite
    df = coerce_numeric_columns(df)
    
    db_table_name = sanitize_name(f"{filename}_default_table")
    chunks = []
    
    # ── Tier A: Row-Level JSON ──────────────────────────
    dtypes_dict = {col: str(dtype) for col, dtype in df.dtypes.items()}
    for idx, row in df.iterrows():
        row_dict = {}
        for col in df.columns:
            val = row[col]
            row_dict[col] = to_json_serializable(val)
                
        row_json = json.dumps({
            "file": filename,
            "sheet": "default",
            "table": "table_1",
            "row": int(idx) + 1,
            **row_dict
        }, separators=(',', ':'))
        
        chunks.append({
            "text": row_json,
            "metadata": {
                "source": file_path,
                "source_name": filename,
                "source_file": filename,
                "type": "excel",
                "sheet_name": "default",
                "table_id": "table_1",
                "row_range": [int(idx) + 1, int(idx) + 1],
                "columns": list(df.columns),
                "column_dtypes": dtypes_dict,
                "value_source": "cached",
                "excluded_hidden_rows": 0,
                "chunk_tier": "row_json",
                "is_summary_row": False,
                "is_flagged": False
            }
        })
        
    # ── Tier B: Row-Group Markdown ──────────────────────
    if len(df) < 5000:
        window_size = 15
        for i in range(0, len(df), window_size):
            window_df = df.iloc[i:i+window_size].fillna("")
            window_df = window_df.copy()
            for col in window_df.columns:
                if window_df[col].dtype == object:
                    window_df[col] = window_df[col].astype(str).str.replace('\n', ' ', regex=False).str.replace('\r', '', regex=False)
            md_table = window_df.to_markdown(index=False)
            chunk_text = f"## File: {filename} (Rows {i+1}-{min(i+window_size, len(df))})\n\n{md_table}"
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "source": file_path,
                    "source_name": filename,
                    "source_file": filename,
                    "type": "excel",
                    "sheet_name": "default",
                    "table_id": "table_1",
                    "row_range": [i + 1, min(i + window_size, len(df))],
                    "columns": list(df.columns),
                    "column_dtypes": dtypes_dict,
                    "value_source": "cached",
                    "excluded_hidden_rows": 0,
                    "chunk_tier": "markdown_window"
                }
            })
            
    return chunks, [(db_table_name, df, "")]

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

def process_excel_file(file_path: str) -> Tuple[List[Dict], List[Tuple[str, pd.DataFrame, str]]]:
    """
    Parse an Excel (.xlsx, .xls) workbook.
    Runs table segmentation to detect multiple side-by-side or stacked tables per sheet.
    Un-merges cells within specific bounds, flattens multi-row headers,
    captures style highlights (bold/red), and filters hidden columns/rows.
    Generates Tier A & Tier B chunks and returns Tier C tables for SQLite.
    """
    filename = os.path.basename(file_path)
    logger.info(f"Ingesting Excel file: {filename}")
    
    chunks = []
    sqlite_tables = []
    
    try:
        # Load twice: once data-only for display values, once formula-only
        with open(file_path, "rb") as f:
            wb_val = openpyxl.load_workbook(f, data_only=True)
        with open(file_path, "rb") as f:
            wb_form = openpyxl.load_workbook(f, data_only=False)
    except Exception as e:
        logger.error(f"Failed to load workbook for {filename}: {e}")
        return [], []
        
    for sheet_name in wb_val.sheetnames:
        sheet_val = wb_val[sheet_name]
        if sheet_name not in wb_form.sheetnames:
            continue
        sheet_form = wb_form[sheet_name]
        
        max_row = sheet_val.max_row
        max_column = sheet_val.max_column
        if max_row <= 1 or max_column <= 1:
            continue
            
        # 1. Resolve and copy grid values, copying top-left cell values into merged cells
        grid_val = [[None for _ in range(max_column)] for _ in range(max_row)]
        grid_form = [[None for _ in range(max_column)] for _ in range(max_row)]
        
        for r in range(1, max_row + 1):
            for c in range(1, max_column + 1):
                grid_val[r-1][c-1] = sheet_val.cell(row=r, column=c).value
                grid_form[r-1][c-1] = sheet_form.cell(row=r, column=c).value
                
        # Forward-fill merged cells safely strictly within bounds
        for merged_range in sheet_val.merged_cells.ranges:
            min_col, min_row, max_col, max_row_range = merged_range.bounds
            # Cap bounds in case they exceed the dimensions
            min_col = min(min_col, max_column)
            min_row = min(min_row, max_row)
            max_col = min(max_col, max_column)
            max_row_range = min(max_row_range, max_row)
            
            top_left_val = grid_val[min_row-1][min_col-1]
            top_left_form = grid_form[min_row-1][min_col-1]
            
            for r in range(min_row, max_row_range + 1):
                for c in range(min_col, max_col + 1):
                    grid_val[r-1][c-1] = top_left_val
                    grid_form[r-1][c-1] = top_left_form
                    
        # 2. Detect title banner rows (only 1 cell populated in the raw unmerged sheet, but merged across columns)
        banner_rows = set()
        if max_column > 2:
            for r in range(max_row):
                non_empty_raw = sum(1 for c in range(max_column) if sheet_val.cell(row=r+1, column=c+1).value is not None and str(sheet_val.cell(row=r+1, column=c+1).value).strip() != "")
                non_empty_grid = sum(1 for c in range(max_column) if grid_val[r][c] is not None and str(grid_val[r][c]).strip() != "")
                if non_empty_raw == 1 and non_empty_grid > 1:
                    banner_rows.add(r)

        # 3. Build occupancy grid and detect connected components (table segmentation)
        # Exclude banner rows from occupancy grid so they don't bridge side-by-side tables
        occupancy = []
        for r in range(max_row):
            if r in banner_rows:
                occupancy.append([False for _ in range(max_column)])
            else:
                occupancy.append([(grid_val[r][c] is not None and str(grid_val[r][c]).strip() != "")
                                  for c in range(max_column)])
                      
        visited = set()
        components = []
        
        for r in range(max_row):
            for c in range(max_column):
                if occupancy[r][c] and (r, c) not in visited:
                    # Flood-fill: tolerance of 3 rows vertically, adjacent only columns horizontally
                    queue = [(r, c)]
                    visited.add((r, c))
                    comp = []
                    while queue:
                        curr = queue.pop(0)
                        comp.append(curr)
                        
                        # Row gap tolerance up to 3, column gap tolerance 0 (only adjacent)
                        for dr in [-3, -2, -1, 0, 1, 2, 3]:
                            for dc in [-1, 0, 1]:
                                if dr == 0 and dc == 0:
                                    continue
                                nr, nc = curr[0] + dr, curr[1] + dc
                                if 0 <= nr < max_row and 0 <= nc < max_column:
                                    if occupancy[nr][nc] and (nr, nc) not in visited:
                                        visited.add((nr, nc))
                                        queue.append((nr, nc))
                    components.append(comp)
                    
        table_counter = 1
        for comp in components:
            if len(comp) < 4:  # filter out noise / floating notes
                continue
                
            # Find bounds
            rows_in_comp = [cell[0] for cell in comp]
            cols_in_comp = [cell[1] for cell in comp]
            min_r, max_r = min(rows_in_comp), max(rows_in_comp)
            min_c, max_c = min(cols_in_comp), max(cols_in_comp)
            
            table_id = f"table_{table_counter}"
            table_counter += 1
            
            # Find title banners immediately above this table
            table_title_parts = []
            check_r = min_r - 1
            while check_r >= 0:
                if check_r in banner_rows:
                    for c_idx in range(max_column):
                        val = grid_val[check_r][c_idx]
                        if val is not None and str(val).strip() != "":
                            table_title_parts.insert(0, str(val).strip())
                            break
                    check_r -= 1
                else:
                    break
            table_title = " - ".join(table_title_parts) if table_title_parts else ""
            
            # Hidden columns and rows filtering
            filtered_cols_indices = []
            for c_idx in range(min_c, max_c + 1):
                col_letter = openpyxl.utils.get_column_letter(c_idx + 1)
                if sheet_val.column_dimensions[col_letter].hidden:
                    continue
                filtered_cols_indices.append(c_idx)
                
            if not filtered_cols_indices:
                continue

            # 4. Multi-row header detection and flattening
            data_start_row = detect_data_start_row(sheet_val, min_r, max_r, filtered_cols_indices, grid_val)
            
            header = []
            for col_idx in filtered_cols_indices:
                col_header_parts = []
                for h_r in range(min_r, min_r + data_start_row):
                    val = grid_val[h_r][col_idx]
                    if val is not None and str(val).strip() != "" and str(val).strip().lower() != "nan":
                        col_header_parts.append(str(val).strip())
                if not col_header_parts:
                    header.append(f"col_{col_idx - min_c}")
                else:
                    header.append(" ".join(col_header_parts))
                
            # Sanitize headers
            final_columns = []
            seen = {}
            for h in header:
                h_clean = sanitize_name(h)
                if not h_clean:
                    h_clean = "column"
                if h_clean in seen:
                    seen[h_clean] += 1
                    final_columns.append(f"{h_clean}_{seen[h_clean]}")
                else:
                    seen[h_clean] = 1
                    final_columns.append(h_clean)
                    
            # 5. Extract data rows, aligning row headers horizontally for side-by-side tables
            data_rows = []
            excluded_hidden_rows = 0
            is_summary_row_flags = []
            is_flagged_row_flags = []
            value_source_flags = []
            
            for r_idx in range(min_r + data_start_row, max_r + 1):
                if sheet_val.row_dimensions[r_idx + 1].hidden:
                    excluded_hidden_rows += 1
                    continue
                    
                # Read columns
                row_data = []
                row_val_sources = []
                for c_idx in filtered_cols_indices:
                    val = grid_val[r_idx][c_idx]
                    form = grid_form[r_idx][c_idx]
                    
                    # Horizon label alignment: if first column of side table is empty, inherit from sheet Column A
                    if c_idx == min_c and c_idx > 0 and (val is None or str(val).strip() == ""):
                        first_col_val = grid_val[r_idx][0]
                        if first_col_val is not None and isinstance(first_col_val, str) and str(first_col_val).strip() != "":
                            val = first_col_val
                    
                    val_source = "cached"
                    if val is None and isinstance(form, str) and form.startswith("="):
                        val = form
                        val_source = "formula_unevaluated"
                    row_data.append(val)
                    row_val_sources.append(val_source)
                    
                data_rows.append(row_data)
                value_source_flags.append("formula" if "formula_unevaluated" in row_val_sources else "cached")
                
                # Highlight formatting detection (first cell check)
                cell = sheet_val.cell(row=r_idx + 1, column=min_c + 1)
                is_summary = False
                is_flagged = False
                if cell.font and cell.font.bold:
                    is_summary = True
                if cell.fill and cell.fill.start_color and cell.fill.start_color.rgb not in ("00000000", "000000"):
                    is_summary = True
                if cell.font and cell.font.color and cell.font.color.rgb == "FFFF0000":
                    is_flagged = True
                is_summary_row_flags.append(is_summary)
                is_flagged_row_flags.append(is_flagged)
                
            if not data_rows:
                continue
                
            df = pd.DataFrame(data_rows, columns=final_columns)
            
            # If key-value block, convert to key/value columns
            if is_key_value_block(df):
                non_empty_cols = [col for col in df.columns if not df[col].isna().all()]
                kv_rows = []
                for _, row in df.iterrows():
                    k = row[non_empty_cols[0]]
                    v = row[non_empty_cols[1]]
                    if pd.isna(k) or str(k).strip() == "":
                        continue
                    k_str = str(k).strip()
                    if k_str.endswith(":"):
                        k_str = k_str[:-1].strip()
                    kv_rows.append([k_str, v])
                df = pd.DataFrame(kv_rows, columns=["key", "value"])
                final_columns = ["key", "value"]
            
            # Try parsing numeric types safely
            df = coerce_numeric_columns(df)
            
            db_table_name = sanitize_name(f"{filename}_{sheet_name}_{table_id}")
            sqlite_tables.append((db_table_name, df, table_title))
            
            dtypes_dict = {col: str(dtype) for col, dtype in df.dtypes.items()}
            
            # ── Tier A Serialization: Row-Level JSON ────────
            for idx in range(len(df)):
                row_dict = {}
                for col in df.columns:
                    val = df.iloc[idx][col]
                    row_dict[col] = to_json_serializable(val)
                        
                row_json = json.dumps({
                    "file": filename,
                    "sheet": sheet_name,
                    "table": table_id,
                    "row": min_r + data_start_row + idx + 1,
                    **row_dict
                }, separators=(',', ':'))
                
                table_title_context = f"Table Title: {table_title}\n" if table_title else ""
                final_text = f"{table_title_context}{row_json}"
                
                chunks.append({
                    "text": final_text,
                    "metadata": {
                        "source": file_path,
                        "source_name": filename,
                        "source_file": filename,
                        "type": "excel",
                        "sheet_name": sheet_name,
                        "table_id": table_id,
                        "row_range": [min_r + data_start_row + idx + 1, min_r + data_start_row + idx + 1],
                        "columns": final_columns,
                        "column_dtypes": dtypes_dict,
                        "value_source": value_source_flags[idx] if idx < len(value_source_flags) else "cached",
                        "excluded_hidden_rows": excluded_hidden_rows,
                        "chunk_tier": "row_json",
                        "is_summary_row": is_summary_row_flags[idx] if idx < len(is_summary_row_flags) else False,
                        "is_flagged": is_flagged_row_flags[idx] if idx < len(is_flagged_row_flags) else False
                    }
                })
                
            # ── Tier B: Row-Group Markdown ────
            if len(df) < 5000:
                window_size = 15
                for i in range(0, len(df), window_size):
                    window_df = df.iloc[i:i+window_size].fillna("")
                    window_df = window_df.copy()
                    for col in window_df.columns:
                        if window_df[col].dtype == object:
                            window_df[col] = window_df[col].astype(str).str.replace('\n', ' ', regex=False).str.replace('\r', '', regex=False)
                    md_table = window_df.to_markdown(index=False)
                    title_hdr = f" (Title: {table_title})" if table_title else ""
                    chunk_text = f"## Sheet: {sheet_name}{title_hdr} (Table: {table_id}, Rows {min_r + data_start_row + i + 1}-{min_r + data_start_row + min(i+window_size, len(df)) + 1})\n\n{md_table}"
                    chunks.append({
                        "text": chunk_text,
                        "metadata": {
                            "source": file_path,
                            "source_name": filename,
                            "source_file": filename,
                            "type": "excel",
                            "sheet_name": sheet_name,
                            "table_id": table_id,
                            "row_range": [min_r + data_start_row + i + 1, min_r + data_start_row + min(i+window_size, len(df)) + 1],
                            "columns": final_columns,
                            "column_dtypes": dtypes_dict,
                            "value_source": "cached",
                            "excluded_hidden_rows": excluded_hidden_rows,
                            "chunk_tier": "markdown_window"
                        }
                    })
                    
        # --- Zero Data Loss: Raw Cell Archive (Tier C) & Standalone Long Cells (Tier D) ---
        raw_dump_cells = []
        for r in range(max_row):
            for c in range(max_column):
                val = grid_val[r][c]
                if val is not None and str(val).strip() != "":
                    text_val = str(val).strip()
                    col_letter = openpyxl.utils.get_column_letter(c + 1)
                    cell_ref = f"Row {r+1}, Col {col_letter}"
                    raw_dump_cells.append(f"[{cell_ref}]: {text_val}")
                    
                    # Tier D: Standalone Long Cells (Bypass Table Logic)
                    if len(text_val) > 100:
                        # Chunk long cells if they exceed 500 characters
                        max_len = 500
                        overlap = 50
                        if len(text_val) <= max_len:
                            sub_chunks = [text_val]
                        else:
                            sub_chunks = []
                            start = 0
                            while start < len(text_val):
                                end = min(start + max_len, len(text_val))
                                sub_chunks.append(text_val[start:end])
                                if end == len(text_val):
                                    break
                                start += (max_len - overlap)
                        
                        for idx, sub_chunk in enumerate(sub_chunks):
                            chunk_text = f"## Sheet: {sheet_name} (Standalone Cell {cell_ref})\n\n{sub_chunk}"
                            chunks.append({
                                "text": chunk_text,
                                "metadata": {
                                    "source": file_path,
                                    "source_name": filename,
                                    "source_file": filename,
                                    "type": "excel",
                                    "sheet_name": sheet_name,
                                    "table_id": "standalone_cell",
                                    "row_range": [r+1, r+1],
                                    "columns": [col_letter],
                                    "column_dtypes": {"cell": "string"},
                                    "value_source": "cached",
                                    "excluded_hidden_rows": 0,
                                    "chunk_tier": "standalone_cell"
                                }
                            })

        # Tier C: Raw Dump chunks
        # Group into blocks of 50 cells
        if raw_dump_cells:
            for i in range(0, len(raw_dump_cells), 50):
                block = raw_dump_cells[i:i+50]
                block_text = "\n".join(block)
                chunk_text = f"## Sheet: {sheet_name} (Raw Dump, Cells {i+1} to {min(i+50, len(raw_dump_cells))})\n\n{block_text}"
                chunks.append({
                    "text": chunk_text,
                    "metadata": {
                        "source": file_path,
                        "source_name": filename,
                        "source_file": filename,
                        "type": "excel",
                        "sheet_name": sheet_name,
                        "table_id": "raw_dump",
                        "row_range": [0, max_row],
                        "columns": [],
                        "column_dtypes": {},
                        "value_source": "raw",
                        "excluded_hidden_rows": 0,
                        "chunk_tier": "raw_dump"
                    }
                })
                    
    return chunks, sqlite_tables

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
