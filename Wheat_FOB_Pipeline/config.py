"""
config.py — Centralized path configuration for the Wheat Arbitrage Analysis System.

Folder architecture
───────────────────
1_Raw_Inputs\          ← drop ALL files here, forever. Never delete anything.
    pdq-export-2026-05-28.csv
    pdq-export-2026-06-07.csv     ← next week arrives alongside the old one
    PR-260529.xlsx
    PR-260607.xlsx
    Hammer_30May26.xlsx
    Hammer_07Jun26.xlsx
    ...

The pipeline automatically selects the NEWEST file per source on every run.
Old files stay in place as a permanent archive and are never touched.

File identification rules (case-insensitive substring match on filename):
    PDQ      — newest .csv  containing "pdq"
    USW      — newest .xlsx containing "pr-"
    Hammer   — newest .xlsx containing "hammer"

To change a rule edit the _RULES list below.
"""

from pathlib import Path

# ── Root ──────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\FOB_Price_Analysis")

# ── Single input folder — archive + active files live here together ───────────
RAW_INPUTS_DIR = ROOT_DIR / "FOB_Price_Analysis_DB" / "1_Raw_Inputs"

# ── SQLite databases ──────────────────────────────────────────────────────────
DB_PDQ     = ROOT_DIR / "db_pdq.db"
DB_USW     = ROOT_DIR / "db_usw.db"
DB_HAMMER  = ROOT_DIR / "db_hammer.db"
DB_SUMMARY = ROOT_DIR / "global_wheat_summary.db"
DB_ARCHIVE = ROOT_DIR / "global_wheat_history.db"   # append-only historical archive

# ── Configuration / Lookup Engine ─────────────────────────────────────────────
CAN_COSTING_XLSX = ROOT_DIR / "CAN_Costing_Input.xlsx"

# ── File identification rules ─────────────────────────────────────────────────
# (config_variable, display_label, file_extension, substring_in_filename)
_RULES = [
    ("PDQ_CSV",     "PDQ CSV",          ".csv",  "pdq"),
    ("USW_XLSX",    "USW XLSX",         ".xlsx", "pr-"),
    ("HAMMER_XLSX", "Hammersmith XLSX", ".xlsx", "hammer"),
]


def _find_newest(label: str, extension: str, substring: str) -> Path:
    """
    Scan RAW_INPUTS_DIR and return the most recently modified file whose
    name (case-insensitive) has `extension` and contains `substring`.

    'Most recently modified' means the file with the latest mtime — i.e.
    the one you dropped in most recently, regardless of the date in its name.

    Raises a descriptive FileNotFoundError if nothing matches.
    """
    if not RAW_INPUTS_DIR.exists():
        raise FileNotFoundError(
            f"\n  Input folder not found:\n    {RAW_INPUTS_DIR}\n"
            f"  Please create it and place your source files inside."
        )

    all_files = [f for f in RAW_INPUTS_DIR.iterdir() if f.is_file()]
    matches   = [
        f for f in all_files
        if f.suffix.lower() == extension.lower()
        and substring.lower() in f.name.lower()
    ]
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # newest first

    if not matches:
        present = [f.name for f in sorted(all_files, key=lambda f: f.name)]
        raise FileNotFoundError(
            f"\n  {label} not found.\n"
            f"  Looking for: a {extension} file whose name contains '{substring}'\n"
            f"  Folder: {RAW_INPUTS_DIR}\n"
            f"  Files present:\n"
            + ("\n".join(f"    • {n}" for n in present)
               if present else "    (folder is empty)")
        )

    # Always show which file was selected so the user can verify
    selected = matches[0]
    older    = matches[1:]

    status = f"  ✓  {label:22s}  →  {selected.name}"
    if older:
        status += f"   ({len(older)} older file(s) archived)"
    print(status)

    return selected


def _resolve_all():
    errors = []
    for var_name, label, ext, substr in _RULES:
        try:
            globals()[var_name] = _find_newest(label, ext, substr)
        except FileNotFoundError as exc:
            globals()[var_name] = None
            errors.append(str(exc))

    if errors:
        raise FileNotFoundError(
            "\n\nCould not locate one or more source files:\n"
            + "\n".join(errors)
        )


# ── Module-level variables (filled by _resolve_all) ──────────────────────────
PDQ_CSV     = None
USW_XLSX    = None
HAMMER_XLSX = None

print("\n  Locating source files (newest per source) …")
_resolve_all()
print()
