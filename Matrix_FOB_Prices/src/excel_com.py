"""Excel automation helpers (Windows, pywin32). Excel itself recalculates and refreshes Power Query;
Python never rewrites the workbook XML after the template is wired."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("excel_com")


def _same(a: str, b: Path) -> bool:
    """Excel reports OneDrive-synced files by URL (https://d.docs.live.net/...), so compare the trailing
    path segments (folder + file name) rather than the full local path."""
    if a.lower().startswith(("http://", "https://")):
        tail = a.replace("\\", "/").rstrip("/").split("/")[-2:]
        return [t.lower() for t in tail] == [b.parent.name.lower(), b.name.lower()]
    return Path(a).resolve().as_posix().lower() == b.resolve().as_posix().lower()


def find_open_workbook(path: Path):
    """The workbook object if `path` is already open in the user's running Excel, else None."""
    import win32com.client
    try:
        xl = win32com.client.GetActiveObject("Excel.Application")
    except Exception:
        return None
    for wb in xl.Workbooks:
        if _same(wb.FullName, path):
            return wb
    return None


@contextmanager
def open_workbook(path: Path, visible: bool = False):
    """Yield (excel_app, workbook): the user's open copy if there is one, else a private hidden instance."""
    import win32com.client
    wb = find_open_workbook(path)
    if wb is not None:
        yield wb.Application, wb
        return
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = visible
    xl.DisplayAlerts = False
    wb = xl.Workbooks.Open(str(path))
    try:
        yield xl, wb
    finally:
        wb.Close(SaveChanges=False)
        xl.Quit()


def refresh_workbook(path: Path) -> None:
    """Refresh All (Power Query), recalculate and save, so the saved file carries current values."""
    with open_workbook(path) as (xl, wb):
        wb.RefreshAll()
        xl.CalculateUntilAsyncQueriesDone()
        xl.CalculateFull()
        wb.Save()
        log.info("refreshed and saved %s", path.name)


if __name__ == "__main__":       # python src/excel_com.py  ->  Refresh All + save the calculator
    import config
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    refresh_workbook(config.WORKBOOK)
