import os
import logging

MAX_CHARS = 4096
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def extract(file_path: str) -> str:
    """
    Extract up to 4096 characters of text from a file.

    Returns "" for:
      - directories
      - files > 10 MB
      - images, binaries, archives
      - any extraction failure (broad try/except — non-fatal)

    Supported types:
      .pdf   → pypdf
      .docx  → python-docx
      .xlsx .xlsm .xltx .xltm → openpyxl (OOXML; sheet 1 only)
      .xls   → xlrd (old binary format; sheet 1 only)
      .txt .md .csv → stdlib open()
    """
    if os.path.isdir(file_path):
        return ""

    try:
        size = os.path.getsize(file_path)
    except OSError:
        return ""

    if size > MAX_FILE_SIZE:
        return ""

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            return _extract_pdf(file_path)
        elif ext == ".docx":
            return _extract_docx(file_path)
        elif ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
            # .xlsm/.xltm are the same OOXML zip as .xlsx plus a macro part;
            # openpyxl reads them directly (data_only ignores the VBA).
            return _extract_xlsx(file_path)
        elif ext == ".xls":
            return _extract_xls(file_path)
        elif ext in (".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml"):
            return _extract_text(file_path)
        else:
            return ""
    except Exception as e:
        logging.debug(f"ContentExtractor: failed on {file_path}: {e}")
        return ""


# ------------------------------------------------------------------
# Format-specific extractors
# ------------------------------------------------------------------

def _extract_pdf(file_path: str) -> str:
    import pypdf
    reader = pypdf.PdfReader(file_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
        if len(text) >= MAX_CHARS:
            break
    return text[:MAX_CHARS]


def _extract_docx(file_path: str) -> str:
    import docx
    doc = docx.Document(file_path)
    text = "\n".join(para.text for para in doc.paragraphs)
    return text[:MAX_CHARS]


def _extract_xlsx(file_path: str) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    parts = []
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if cell is not None:
                parts.append(str(cell))
        joined = " ".join(parts)
        if len(joined) >= MAX_CHARS:
            break
    return " ".join(parts)[:MAX_CHARS]


def _extract_xls(file_path: str) -> str:
    # Old binary Excel (.xls). openpyxl only reads .xlsx; xlrd 2.x only reads .xls —
    # the two cover the format split. Sheet 1 only, to match _extract_xlsx.
    import xlrd
    book = xlrd.open_workbook(file_path)
    sheet = book.sheet_by_index(0)
    parts = []
    for r in range(sheet.nrows):
        for cell in sheet.row_values(r):
            if cell not in (None, ""):
                parts.append(str(cell))
        if len(" ".join(parts)) >= MAX_CHARS:
            break
    return " ".join(parts)[:MAX_CHARS]


def _extract_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(MAX_CHARS)
