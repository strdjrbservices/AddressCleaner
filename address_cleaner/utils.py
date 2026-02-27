import pandas as pd
import re
import usaddress
import io

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

def process_data_file(file_obj, street_col, unit_col=None, city_col=None, state_col=None, zip_col=None):
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

    # Clean addresses before parsing
    df = clean_addresses(df, street_col)
    
    parsed_rows = []

    for index, row in df.iterrows():
        address_str = row[street_col]
        if pd.isna(address_str):
            continue
        
        address_str = str(address_str)
        
        try:
            parsed_data, _ = usaddress.tag(address_str)

            # Combine components for Address Line 1
            address_line_1_parts = [
                parsed_data.get("AddressNumber", ""),
                parsed_data.get("StreetNamePreDirectional", ""),
                parsed_data.get("StreetName", ""),
                parsed_data.get("StreetNamePostType", ""),
                parsed_data.get("StreetNamePostDirectional", "")
            ]
            address_line_1 = " ".join(part for part in address_line_1_parts if part).strip()

            # Combine components for Address Line 2
            if unit_col and unit_col in df.columns and pd.notna(row[unit_col]):
                address_line_2 = str(row[unit_col]).strip()
            else:
                address_line_2 = parsed_data.get("OccupancyIdentifier", "").strip()

            # Get remaining components
            placename = str(row[city_col]) if city_col and city_col in df.columns and pd.notna(row[city_col]) else parsed_data.get("PlaceName", "")
            statename = str(row[state_col]) if state_col and state_col in df.columns and pd.notna(row[state_col]) else parsed_data.get("StateName", "")
            zipcode = str(row[zip_col]) if zip_col and zip_col in df.columns and pd.notna(row[zip_col]) else parsed_data.get("ZipCode", "")

            parsed_rows.append([address_line_1, address_line_2, placename, statename, zipcode])

        except usaddress.RepeatedLabelError:
            # Fallback for ambiguous addresses
            placename = str(row[city_col]) if city_col and city_col in df.columns and pd.notna(row[city_col]) else ""
            statename = str(row[state_col]) if state_col and state_col in df.columns and pd.notna(row[state_col]) else ""
            zipcode = str(row[zip_col]) if zip_col and zip_col in df.columns and pd.notna(row[zip_col]) else ""
            parsed_rows.append([address_str, "", placename, statename, zipcode])

    # Create DataFrame and save to BytesIO
    clean_df = pd.DataFrame(parsed_rows, columns=["AddressLine1", "AddressLine2", "City", "State", "Zip Code"])
    output = io.BytesIO()
    clean_df.to_excel(output, index=False)
    output.seek(0)
    return output
