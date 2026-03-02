import pandas as pd
import re
import usaddress
import io
from django.core.cache import cache
from func_timeout import func_timeout, FunctionTimedOut

# Add pypostal with a fallback if not installed
try:
    from postal.parser import parse_address
    PYPOSTAL_AVAILABLE = True
except ImportError:
    PYPOSTAL_AVAILABLE = False

# Add deepparse with a fallback
try:
    from deepparse.parser import AddressParser
    DEEPPARSE_AVAILABLE = True
except ImportError:
    DEEPPARSE_AVAILABLE = False

# Global cache for Deepparse model
_DEEPPARSE_MODEL = None

def get_deepparse_model():
    global _DEEPPARSE_MODEL
    if _DEEPPARSE_MODEL is None:
        _DEEPPARSE_MODEL = AddressParser(model_type="bpemb", device="cpu")
    return _DEEPPARSE_MODEL

def clean_addresses(df, address_col):
    """
    Cleans and standardizes addresses.
    """
    # 0. Handle split addresses (e.g. "776 - 778 25th Ave" -> "776 25th Ave" & "778 25th Ave")
    def split_dual_addresses(addr):
        if isinstance(addr, str):
            # Matches patterns like "776 - 778", "776, 778", "776 / 778" followed by street
            match = re.match(r'^(\d+)\s*[-,\/]\s*(\d+)\s+(.*)', addr)
            if match:
                num1, num2, rest = match.groups()
                return [f"{num1} {rest}", f"{num2} {rest}"]
        return [addr]

    df[address_col] = df[address_col].apply(split_dual_addresses)
    df = df.explode(address_col).reset_index(drop=True)

    def apply_cleaning(addr):
        if not isinstance(addr, str):
            return addr
        
        # 1. Remove duplicate house numbers (e.g., "3415 3415 CESAR CHAVEZ")
        addr = re.sub(r'^(\d+)\s+\1', r'\1', addr)
        
        # 2. Fix redundant suffixes (e.g., "ST ST" or "JERSEY ST. ST")
        addr = re.sub(r'\b(ST|AVE|BLVD|RD|LN|DR|CT|WAY)\.?\s+\1\b', r'\1', addr, flags=re.IGNORECASE)
        
        # 3. Standardize "Unit/Apt" formatting for PostGrid
        addr = re.sub(r'\s+([A-Z]|\d+)$', r' #\1', addr)
        
        # 4. Strip extra whitespace
        return addr.strip()

    df[address_col] = df[address_col].apply(apply_cleaning)
    return df

def process_data_file(file_obj, street_col, unit_col=None, city_col=None, state_col=None, zip_col=None, parser_choice='usaddress', task_id=None):
    if file_obj.size == 0:
        raise ValueError("The uploaded file is empty.")

    filename = file_obj.name.lower()
    if filename.endswith(('.csv', '.tsv', '.txt')):
        sep = '\t' if filename.endswith(('.tsv', '.txt')) else ','
        try:
            df = pd.read_csv(file_obj, sep=sep)
        except UnicodeDecodeError:
            file_obj.seek(0)
            # Fallback to latin-1 which is common for Excel-generated files
            df = pd.read_csv(file_obj, sep=sep, encoding='latin-1')
    else:
        df = pd.read_excel(file_obj)
    
    if df.empty:
        raise ValueError("The uploaded file contains no data rows.")

    if street_col not in df.columns:
        raise ValueError(f"Column '{street_col}' not found in the uploaded file.")

    # Check for pypostal availability if chosen
    if parser_choice == 'pypostal' and not PYPOSTAL_AVAILABLE:
        raise ImportError("pypostal library is not installed. Please run 'pip install pypostal' and download its data to use this feature.")

    # Check for deepparse availability
    if parser_choice == 'deepparse' and not DEEPPARSE_AVAILABLE:
        raise ImportError("Deepparse library is not installed. Please run 'pip install deepparse' to use this feature.")

    # Initialize deepparse if selected (loads model, which can be slow)
    dp_parser = None
    if parser_choice == 'deepparse':
        dp_parser = get_deepparse_model()

    # Clean addresses before parsing
    df = clean_addresses(df, street_col)
    
    total_rows = len(df)
    parsed_rows = []

    for index, row in df.iterrows():
        # Update progress in cache
        if task_id:
            # Update every 5 rows or if it's the last one to reduce cache write overhead
            if index % 5 == 0 or index == total_rows - 1:
                progress = int((index + 1) / total_rows * 100)
                cache.set(f'progress_{task_id}', progress, 300) # Expire in 5 minutes

        address_str = row[street_col]
        if pd.isna(address_str):
            continue
        
        address_str = str(address_str)
        
        # Get values from other columns first, as they have priority
        unit_col_val = str(row[unit_col]) if unit_col and unit_col in df.columns and pd.notna(row[unit_col]) else ""
        placename_col = str(row[city_col]) if city_col and city_col in df.columns and pd.notna(row[city_col]) else ""
        statename_col = str(row[state_col]) if state_col and state_col in df.columns and pd.notna(row[state_col]) else ""
        zipcode_col = str(row[zip_col]) if zip_col and zip_col in df.columns and pd.notna(row[zip_col]) else ""

        if parser_choice == 'usaddress':
            try:
                # Add a 5-second timeout to prevent hanging on a single address
                parsed_data, _ = func_timeout(5, usaddress.tag, args=(address_str,))

                # Combine components for Address Line 1
                address_line_1_parts = [
                    parsed_data.get("AddressNumber", ""),
                    parsed_data.get("StreetNamePreDirectional", ""),
                    parsed_data.get("StreetName", ""),
                    parsed_data.get("StreetNamePostType", ""),
                    parsed_data.get("StreetNamePostDirectional", "")
                ]
                address_line_1 = " ".join(part for part in address_line_1_parts if part).strip()

                # Prioritize column value, then parsed value
                address_line_2 = unit_col_val if unit_col_val else parsed_data.get("OccupancyIdentifier", "").strip()
                placename = placename_col if placename_col else parsed_data.get("PlaceName", "")
                statename = statename_col if statename_col else parsed_data.get("StateName", "")
                zipcode = zipcode_col if zipcode_col else parsed_data.get("ZipCode", "")

                parsed_rows.append([address_line_1, address_line_2, placename, statename, zipcode])

            except (usaddress.RepeatedLabelError, FunctionTimedOut):
                # Fallback for ambiguous or timed-out addresses, using column values if available
                parsed_rows.append([address_str, unit_col_val, placename_col, statename_col, zipcode_col])
        
        elif parser_choice == 'pypostal':
            try:
                # Add a 5-second timeout
                parsed_list = func_timeout(5, parse_address, args=(address_str,))
                parsed_data = {key: value for value, key in parsed_list}

                # Combine components for Address Line 1
                address_line_1 = f"{parsed_data.get('house_number', '')} {parsed_data.get('road', '')}".strip()
                
                if not address_line_1 and 'po_box' in parsed_data:
                    address_line_1 = f"PO BOX {parsed_data.get('po_box')}"

                # If parsing fails to find a street, use the original string as a fallback
                if not address_line_1:
                    address_line_1 = address_str

                # Prioritize column value, then parsed value
                address_line_2 = unit_col_val if unit_col_val else parsed_data.get("unit", "").strip()
                placename = placename_col if placename_col else parsed_data.get("city", "")
                statename = statename_col if statename_col else parsed_data.get("state", "")
                zipcode = zipcode_col if zipcode_col else parsed_data.get("postcode", "")

                parsed_rows.append([address_line_1, address_line_2, placename, statename, zipcode])
            except (FunctionTimedOut, Exception):
                # Broad fallback for any pypostal error or timeout
                parsed_rows.append([address_str, unit_col_val, placename_col, statename_col, zipcode_col])
        
        elif parser_choice == 'deepparse':
            try:
                # Add a 5-second timeout
                parsed = func_timeout(5, dp_parser, args=(address_str,))
                
                # Construct Address Line 1 from Deepparse fields
                # Fields: StreetNumber, Orientation, StreetName
                parts = [
                    parsed.StreetNumber,
                    parsed.Orientation,
                    parsed.StreetName
                ]
                address_line_1 = " ".join(p for p in parts if p).strip()
                
                if not address_line_1:
                    address_line_1 = address_str

                address_line_2 = unit_col_val if unit_col_val else (parsed.Unit if parsed.Unit else "").strip()
                placename = placename_col if placename_col else (parsed.Municipality if parsed.Municipality else "")
                statename = statename_col if statename_col else (parsed.Province if parsed.Province else "")
                zipcode = zipcode_col if zipcode_col else (parsed.PostalCode if parsed.PostalCode else "")
                
                parsed_rows.append([address_line_1, address_line_2, placename, statename, zipcode])
            except (FunctionTimedOut, Exception):
                # Broad fallback for any deepparse error or timeout
                parsed_rows.append([address_str, unit_col_val, placename_col, statename_col, zipcode_col])

    # Create DataFrame and save to BytesIO
    clean_df = pd.DataFrame(parsed_rows, columns=["AddressLine1", "AddressLine2", "City", "State", "Zip Code"])
    output = io.BytesIO()
    clean_df.to_excel(output, index=False)
    output.seek(0)
    return output
