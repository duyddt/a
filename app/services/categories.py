"""Danh mục (thư mục chủ đề) của văn bản, theo cách bộ dữ liệu được sắp: mỗi chủ đề là một thư mục "I. Quản lý thuế",
"II. Hóa đơn điện tử"… và tệp danh mục Excel (Danh_muc_van_ban_phap_luat.xlsx) ghi thứ tự văn bản trong từng chủ đề.

- Chủ đề sắp theo số La Mã I, II, … X (không theo bảng chữ cái như trình duyệt tệp: "IX" đứng sau "VIII", không sau
  "IV"). Thư mục không đánh số đứng sau cùng, theo tên.
- Văn bản trong một chủ đề: theo cột STT của tệp danh mục; tệp "phần tiếp" (L59_2020_QH14_tiep) đứng ngay sau phần
  chính; văn bản không có trong danh mục đứng sau, theo thứ bậc Luật → Nghị định → Thông tư → khác.
- Một văn bản có thể thuộc nhiều chủ đề (ND141 ở cả "V. Thuế TNDN" và "VI. Hộ kinh doanh").
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from .legal_parser import from_file_name
from .pdf_parser import document_id_from_filename

NUMBERED_FOLDER = re.compile(r"^\s*([IVXLCDM]+)\.\s*(\S.*?)\s*$")
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
UNNUMBERED_POSITION = 1000  # thư mục không đánh số La Mã: sau mọi thư mục có số
TYPE_RANK = {"Luật": 1, "Nghị định": 2, "Thông tư": 3}  # văn bản ngoài danh mục: theo thứ bậc văn bản
OUTSIDE_CATALOG = 1_000_000
SHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PROGRESS_KEYS = ("total", "approved", "flagged", "structure_error", "unreviewed")


def _roman_value(text: str) -> int | None:
    """Giá trị của số La Mã viết đúng chuẩn ("IV" = 4, "IX" = 9); None nếu không phải ("IIII", "VX")."""
    total = 0
    for index, char in enumerate(text):
        value = ROMAN[char]
        following = ROMAN[text[index + 1]] if index + 1 < len(text) else 0
        total += -value if value < following else value
    return total if total > 0 and _to_roman(total) == text else None


def _to_roman(number: int) -> str:
    parts = []
    for value, symbol in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                          (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        count, number = divmod(number, value)
        parts.append(symbol * count)
    return "".join(parts)


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text.replace("Đ", "D").replace("đ", "d"))
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_") or "khac"


@dataclass(frozen=True)
class Category:
    code: str  # mã ngắn dùng trên URL: số La Mã ("IV"), hoặc tên bỏ dấu với thư mục không đánh số
    name: str  # "Thuế thu nhập cá nhân"
    position: int  # thứ tự hiển thị

    @property
    def numbered(self) -> bool:
        return self.position < UNNUMBERED_POSITION

    @property
    def label(self) -> str:
        """Tên hiển thị, giống tên thư mục: "IV. Thuế thu nhập cá nhân"."""
        return f"{self.code}. {self.name}" if self.numbered else self.name

    def sort_key(self) -> tuple[int, str]:
        return self.position, self.name


def category_from_folder(folder_name: str) -> Category:
    """Danh mục từ tên thư mục của bộ dữ liệu: "IV. Thuế thu nhập cá nhân" -> (IV, Thuế thu nhập cá nhân, 4)."""
    match = NUMBERED_FOLDER.match(folder_name)
    if match and (value := _roman_value(match.group(1))):
        return Category(match.group(1), match.group(2), value)
    name = " ".join(folder_name.split())
    return Category(_slug(name), name, UNNUMBERED_POSITION)


def read_catalog_order(path: Path) -> dict[str, int]:
    """{mã văn bản (theo tên tệp): STT} từ tệp danh mục .xlsx (cột "STT" và "Tên file…"). Đọc bằng thư viện chuẩn;
    tệp không phải danh mục hoặc hỏng thì trả {}."""
    try:
        with zipfile.ZipFile(path) as book:
            names = set(book.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter(f"{{{SHEET_NS['m']}}}t"))
                          for item in root.findall("m:si", SHEET_NS)]
            sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError):
        return {}

    rows = []
    for row in sheet.iter(f"{{{SHEET_NS['m']}}}row"):
        cells = {}
        for cell in row.findall("m:c", SHEET_NS):
            column = re.match(r"[A-Z]+", cell.get("r", "")).group(0) if cell.get("r") else str(len(cells))
            value = cell.find("m:v", SHEET_NS)
            if cell.get("t") == "s" and value is not None:
                cells[column] = shared[int(value.text)]
            elif cell.get("t") == "inlineStr":
                cells[column] = "".join(node.text or "" for node in cell.iter(f"{{{SHEET_NS['m']}}}t"))
            elif value is not None:
                cells[column] = value.text or ""
        rows.append(cells)

    header = next((row for row in rows if any(str(v).strip().lower() == "stt" for v in row.values())), None)
    if header is None:
        return {}
    number_col = next(col for col, value in header.items() if str(value).strip().lower() == "stt")
    file_col = next((col for col, value in header.items() if str(value).strip().lower().startswith("tên file")), None)
    if file_col is None:
        return {}
    order = {}
    for row in rows[rows.index(header) + 1:]:
        file_name, number = str(row.get(file_col, "")).strip(), str(row.get(number_col, "")).strip()
        match = re.match(r"([^\s/\\]+?)\.pdf\b", file_name, re.IGNORECASE)
        if match and re.fullmatch(r"\d+(?:\.0+)?", number):
            order.setdefault(document_id_from_filename(match.group(1) + ".pdf"), int(float(number)))
    return order


@dataclass
class Folder:
    """Một thư mục ở màn hình danh sách: danh mục và các văn bản của nó theo đúng thứ tự."""

    category: Category
    documents: list = field(default_factory=list)  # DocumentSummary

    @property
    def unit_count(self) -> int:
        return sum(document.unit_count or 0 for document in self.documents)

    @property
    def page_count(self) -> int:
        return sum(document.page_count or 0 for document in self.documents)

    @property
    def progress(self) -> dict | None:
        """Tiến độ cộng dồn của cả thư mục (cùng dạng với tiến độ một văn bản); None khi không có số liệu thẩm định."""
        parts = [document.progress for document in self.documents if document.progress]
        if not parts:
            return None
        counts = {key: sum(part[key] for part in parts) for key in PROGRESS_KEYS}
        counts["percent_approved"] = round(100 * counts["approved"] / counts["total"]) if counts["total"] else 0
        return counts


def group_into_folders(items: list, categories: list[Category]) -> tuple[list[Folder], list]:
    """(thư mục theo thứ tự I → X, văn bản không thuộc thư mục nào). Văn bản ở nhiều danh mục có mặt ở mỗi thư mục."""
    folders = {category.code: Folder(category) for category in sorted(categories, key=Category.sort_key)}
    loose = []
    for item in items:
        codes = [code for code in item.categories if code in folders]
        if not codes:
            loose.append(item)
        for code in codes:
            folders[code].documents.append(item)
    for folder in folders.values():
        folder.documents.sort(key=lambda document, code=folder.category.code: (document.categories[code],
                                                                               document.document_id))
    return list(folders.values()), loose


def position_in_folder(document_id: str, catalog: dict[str, int]) -> int:
    """Khóa sắp xếp của văn bản trong thư mục chủ đề (nhỏ đứng trước; bằng nhau thì theo mã văn bản)."""
    if document_id in catalog:
        return catalog[document_id] * 10
    main = max((stem for stem in catalog if document_id.startswith(stem + "_")), key=len, default=None)
    if main is not None:  # "L59_2020_QH14_tiep": ngay sau "L59_2020_QH14"
        return catalog[main] * 10 + 5
    _, document_type = from_file_name(document_id)
    return OUTSIDE_CATALOG + TYPE_RANK.get(document_type, 9) * 1000
