"""Tách văn bản quy phạm pháp luật (Điều → Khoản → Điểm) và siêu dữ liệu từ chữ của từng trang PDF.

Nguyên tắc "không đoán" (cùng tinh thần scripts/build_legal_document.py):
- Đọc liền mạch qua ranh giới trang. Trang của mỗi đơn vị là các trang có dòng chữ của đơn vị đó. Bỏ dòng tiêu đề
  trang Công báo và số trang ở mép trang.
- Chỉ nhận Điều/Khoản/Điểm khi ký hiệu đứng đầu dòng ("Điều N.", "N.", "a)") và đúng thứ tự. Nhảy số ít vẫn nhận
  nhưng đặt is_confident=false. Số sai thứ tự được coi là chữ tiếp nối, và đơn vị chứa nó bị đánh dấu.
- Không tách trong phần trích dẫn “…” (luật sửa đổi trích nguyên văn Điều/Khoản/Điểm của văn bản khác). Ngoặc mở
  giữa câu mà không đóng (OCR hay chèn ngoặc lạ) thì hết hạn sau vài dòng, xem _Quotes.
- Dừng gắn chữ vào đơn vị khi gặp Chương/Mục mới, công thức kết thúc "./.", "Nơi nhận:", khối chữ ký.
- Siêu dữ liệu (số hiệu, loại, tên, ngày ban hành, ngày hiệu lực) đọc từ chính văn bản. Không thấy thì trả None.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.contracts import LegalUnit, make_unit_id

LETTERS = "abcdđeghiklmnopqrstuvxy"  # thứ tự ký hiệu Điểm (không có f, j, w, z)
ARTICLE_GAP = 3  # nhận "Điều N" khi N ≤ Điều trước + 3: OCR có thể làm mất tối đa 2 tiêu đề Điều (có đánh dấu)
MAX_QUOTE_LINES = 200  # ngoặc khối mở quá số dòng này coi như OCR làm mất ngoặc đóng
INLINE_QUOTE_LINES = 8  # ngoặc mở giữa câu mà quá số dòng này chưa đóng: coi là dấu lạ do OCR chèn vào
QUOTE_STARTERS = "([“‘.…"  # ký tự (ngoài chữ và số) có thể đứng ngay sau dấu mở ngoặc
QUOTE_LEAD_JUNK = " _|~•·*^`-–—"  # ký tự có thể đứng trước dấu mở ngoặc ở đầu dòng

ARTICLE = re.compile(r"^[ĐÐ]iều\s+(\d+)\s*\.\s*(.*)$")
# "1.Tổ chức": OCR hay nuốt dấu cách sau số Khoản; "1.1." (tiểu mục) và "2.000" (số) thì không phải Khoản
CLAUSE = re.compile(r"^(\d+)\.(?:\s+\S|(?=[^\W\d_]))")
POINT = re.compile(r"^([a-zđ])\)\s+\S")
CHAPTER = re.compile(r"^Chương\s+([IVXLCDM]+)\b(.*)$")
SECTION = re.compile(r"^Mục\s+(\d+)\b(.*)$")
PAGE_HEADER = re.compile(r"^(?:\d{1,4}\s+)?CÔNG BÁO\s*/.*$|^-?\s*\d{1,4}\s*-?$")
CLOSING = re.compile(
    r"^(?:Nơi\s+nhận\s*:|(?:TM|KT|TL|TUQ|Q)\.\s+\S|CHỦ\s+TỊCH\b|BỘ\s+TRƯỞNG\b|THỦ\s+TƯỚNG\b"
    r"|(?:Luật|Bộ luật|Pháp lệnh|Nghị quyết) này (?:đã )?được (?:Quốc hội|Ủy ban))"
)
# vết bẩn / gạch lề mà OCR đọc thành ký tự ở đầu dòng; dấu " chỉ tính là nhiễu khi không dính vào chữ phía sau
OCR_NOISE = re.compile(r'^(?:[_|~•·*^`,]|"(?=[\s_|~•·*^`]))+\s*')
# ký tự lạ OCR đọc ra trước tiêu đề Điều (” Điều 4., 'Điều 14., „ Điều 16., - Điều 143.); số Điều vẫn phải đúng thứ tự
# tiêu đề Điều bị OCR đọc lệch: "Điều. 51.", "Điều, 31.", "Điều 166:", "Điền 147.". Chỉ nhận khi đúng số Điều kế tiếp.
# Sau số Điều phải là "." hoặc ":"; dấu "," là câu tham chiếu xuống hàng ("Điều 5, Điều 6 Thông tư này…")
LOOSE_ARTICLE = re.compile(r"^(?:[ĐÐ]iều\s*[.,]|[ĐÐ]i(?:ều|ền|ểu|êu|ếu))\s*(\d+)\s*[.:]\s*(.*)$")
ARTICLE_JUNK = re.compile(r"^[”„'’‘\-–—.,:;\s]{1,3}(?=[ĐÐ]iều\s+\d)")
# dấu mở ngoặc không thuộc trích dẫn khối đứng trước ký hiệu cấu trúc (“Điều 3. …, "dân cư…): bỏ khi nhận dạng
# câu dẫn của phần sửa đổi, kể cả khi OCR làm mất hay đọc lệch dấu ":" ("…Điều 2 như sau" ⏎ “Điều 2. …")
LEAD_IN = re.compile(r"(?:như\s+sau|sau\s+đây)\s*[:.;,]?\s*$")
LEADING_QUOTE = re.compile(r"^[“\"‘']+\s*")
# ký hiệu Điểm bị Tesseract đọc thành ký tự không có trong tiếng Việt: ©) là c), ø) / gø) là g)
OCR_POINT_MISREADS = {"©": "c", "ø": "g", "gø": "g"}
OCR_POINT = re.compile(r"^(©|gø|ø)(?=\)\s)")

DATE = r"ngày\s+(?:(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})|(\d{1,2})/(\d{1,2})/(\d{4}))"
DOCUMENT_NO = re.compile(r"\b[Ss]ố\s*:\s*(\d+/[\w\-/]*\w)")
ISSUED_IN_HEADER = re.compile(r",\s*" + DATE)
ISSUED_BY_VOTE = re.compile(r"thông\s+qua\s+" + DATE)
EFFECTIVE = re.compile(r"này\s+có\s+hiệu\s+lực(?:\s+thi\s+hành)?\s+(?:kể\s+)?từ\s+" + DATE)
TYPE_HEADINGS = {
    "LUẬT": "Luật",
    "BỘ LUẬT": "Bộ luật",
    "PHÁP LỆNH": "Pháp lệnh",
    "NGHỊ QUYẾT": "Nghị quyết",
    "NGHỊ ĐỊNH": "Nghị định",
    "QUYẾT ĐỊNH": "Quyết định",
    "CHỈ THỊ": "Chỉ thị",
    "THÔNG TƯ": "Thông tư",
    "THÔNG TƯ LIÊN TỊCH": "Thông tư liên tịch",
    "VĂN BẢN HỢP NHẤT": "Văn bản hợp nhất",
}
TITLE_STOPS = ("Căn cứ", "Theo đề nghị", "Chương", "Phần", "Mục", "Điều", "Xét ", "Để ")
MAX_TITLE_LINES = 6
# số hiệu đúng dạng: 48/2024/QH15, 254/2026/NĐ-CP, 101/2025/TT-BNNMT, 15/QĐ-TTg. OCR hay đọc sai (4452020/NĐ-CP)
# bản chưa ban hành để trống số: "Số: /2025/TT-BNNMT". Khi đó không lấy số theo tên tệp
BLANK_DOCUMENT_NO = re.compile(r"\b[Ss]ố\s*:\s*/\d{4}/")
VALID_DOCUMENT_NO = re.compile(r"^\d{1,4}/(?:\d{4}/)?[A-ZĐ][\wĐ]*(?:-[\wĐ]+)*$")
# tên tệp theo quy ước của bộ dữ liệu (Danh_muc_van_ban_phap_luat.xlsx): L48_2024_QH15, ND145_2020, TT101_2025_BNNMT
FILE_NAME_CONVENTION = re.compile(r"^(L|ND|TT)(\d{1,4})_(\d{4})(?:_([A-Z][A-Z0-9]*))?(?:_|$)")
FILE_NAME_TYPES = {"L": "Luật", "ND": "Nghị định", "TT": "Thông tư"}
# câu ban hành ở cuối phần căn cứ, chứa nguyên tên văn bản: "Chính phủ ban hành Nghị định quy định ..."
ENACTING = re.compile(r"\bban\s+hành\s+(?:Bộ\s+luật|Luật|Pháp\s+lệnh|Nghị\s+quyết|Nghị\s+định|Quyết\s+định|Chỉ\s+thị|"
                      r"Thông\s+tư(?:\s+liên\s+tịch)?)\s+(.{2,400}?)\.(?=\s|$)")
ENACTING_WINDOW = 30
# quốc hiệu, tiêu ngữ: không bao giờ là tên văn bản
MOTTO = re.compile(r"CỘNG\s+H[OÒ][AÀ]\s+XÃ\s+HỘI|XÃ\s+HỘI\s+CHỦ\s+NGHĨA|Độc\s+lập\s*-\s*Tự\s+do")
# dấu "Cổng thông tin điện tử Chính phủ", "Công văn đến... Giờ... Kính chuyển" đóng đè lên phần tên ở bản scan
STAMP = re.compile(r"THÔNG\s+T\w{0,2}\s+\w?IỆN|CHÍNH\s+PHÙ|CÔNG\s+VĂN\s+ĐẾN|\bGiờ\b|Kính\s+chuyển|VĂN\s+PHÒNG\s+CHÍNH")
TITLE_LEAD = re.compile(r"^[\W\d_]+")
TITLE_TAIL = re.compile(r"[\s,;:_|\-–—]+$")
WORD = re.compile(r"[^\W\d_]{2,}")


@dataclass
class Metadata:
    document_no: str | None = None
    document_type: str | None = None
    title: str | None = None
    issued_date: date | None = None
    effective_from: date | None = None


@dataclass
class _Node:
    number: int | str
    title: str = ""
    chapter: str | None = None
    section: str | None = None
    heading_page: int | None = None
    lines: list[tuple[int, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    @property
    def content(self) -> str:
        return "\n".join(text for _, text in self.lines)

    @property
    def pages(self) -> list[int]:
        pages = {page for page, _ in self.lines}
        if self.heading_page is not None:
            pages.add(self.heading_page)
        return sorted(pages)


@dataclass
class ParsedDocument:
    units: list[LegalUnit]
    metadata: Metadata
    has_text: bool


def page_lines(pages_text: list[str]):
    """(trang, dòng) đã chuẩn hóa NFC, bỏ dòng trống và tiêu đề trang / số trang ở 2 dòng đầu hoặc cuối mỗi trang."""
    for page, text in enumerate(pages_text, start=1):
        lines = [unicodedata.normalize("NFC", line).strip() for line in re.split(r"\r\n|\r|\n", text or "")]
        filled = [index for index, line in enumerate(lines) if line]
        edges = set(filled[:2] + filled[-2:])
        for index, line in enumerate(lines):
            if line and not (index in edges and PAGE_HEADER.match(line)):
                yield page, line


def _same_quotes(text: str) -> str:
    """Văn bản gõ bằng bảng mã cũ dùng hai dấu nháy đơn thay ngoặc kép: ‘‘ … ’’ hoặc '' … ''."""
    return text.replace("‘‘", "“").replace("’’", "”").replace("''", '"')


def _quote_marks(text: str) -> list[tuple[int, bool]]:
    """Các dấu ngoặc kép trong dòng: (vị trí trong _same_quotes(text), True nếu là dấu mở).

    ” là dấu đóng. “ là dấu mở, trừ khi không có chữ theo sau (cuối dòng, “®, “%...): đó là vết bẩn OCR, bỏ qua. Dấu "
    thẳng không có chiều nên xét theo chữ hai bên, như trình soạn thảo tự đổi sang ngoặc cong: dính vào chữ phía trước
    là dấu đóng (“Lý do nộp" hay chữa bệnh".); đứng sau khoảng trắng và dính vào chữ phía sau là dấu mở; khoảng trắng
    cả hai bên, hoặc theo sau là ký hiệu lạ (`, _...), thì không rõ chiều (thường là vết bẩn OCR) nên bỏ qua."""
    text = _same_quotes(text)
    marks = []
    for index, char in enumerate(text):
        if char == "”":
            marks.append((index, False))
        elif char == "“":
            following = text[index + 1:].lstrip()[:1]
            if following and (following.isalnum() or following in QUOTE_STARTERS):
                marks.append((index, True))
        elif char == '"':
            before = text[index - 1] if index else " "
            after = text[index + 1] if index + 1 < len(text) else " "
            if not (before.isspace() or before in "([{:–—-"):
                marks.append((index, False))
            elif after.isalnum() or after in QUOTE_STARTERS:
                marks.append((index, True))
    return marks


def _opens_block(text: str, index: int, previous: str, next_article: int) -> bool:
    """Dấu mở ngoặc ở vị trí `index` (của dòng đã qua _same_quotes) mở một trích dẫn khối, tức đoạn văn bản được sửa
    đổi trích nguyên văn. Đó là khi dấu ngoặc:
    - đứng ngay sau dấu hai chấm cùng dòng, hoặc đầu dòng ngay sau dòng kết thúc bằng ':' ("…như sau:" ⏎ “Điều 5. …");
    - hoặc đứng đầu dòng, ngay trước ký hiệu cấu trúc (“1. …, “a) …, “Điều 5. …), vì câu dẫn sửa đổi không phải lúc
      nào cũng có "như sau:". Riêng “Điều N. với N đúng là Điều kế tiếp của chính văn bản thì là ngoặc lạ do OCR
      đứng trước tiêu đề thật, không phải trích dẫn."""
    before = text[:index].strip(QUOTE_LEAD_JUNK)
    if before.endswith(":"):
        return True
    if before:
        return False
    if previous.rstrip().endswith(":") or LEAD_IN.search(previous):
        return True
    quoted = text[index + 1:].lstrip()
    if heading := ARTICLE.match(quoted):
        return int(heading.group(1)) != next_article
    return bool(CLAUSE.match(quoted) or POINT.match(quoted))


def _leading_quote(text: str, previous: str, next_article: int) -> str | None:
    """Dòng mở đầu bằng dấu mở ngoặc: "block" nếu là trích dẫn khối, "inline" nếu không; None nếu không mở bằng ngoặc."""
    marks = _quote_marks(text)
    if not marks or not marks[0][1]:
        return None
    normalized, index = _same_quotes(text), marks[0][0]
    if normalized[:index].strip(QUOTE_LEAD_JUNK):
        return None
    return "block" if _opens_block(normalized, index, previous, next_article) else "inline"


@dataclass
class _Quotes:
    """Các ngoặc kép đang mở, theo thứ tự mở: [là trích dẫn khối?, số dòng đã qua].

    Trích dẫn khối (_opens_block) có thể dài nhiều trang; bên trong không tách cấu trúc. Ngoặc trong câu chỉ trích vài
    chữ ("cụm từ “…”"). OCR hay chèn dấu ngoặc lạ vào giữa chữ (bảo "hiểm, ngân “hàng) mà không có dấu đóng, nên ngoặc
    trong câu chưa đóng sau INLINE_QUOTE_LINES dòng thì bỏ, và gặp đúng ký hiệu cấu trúc kế tiếp thì đóng luôn."""

    open: list[list] = field(default_factory=list)

    @property
    def block(self) -> bool:
        return any(is_block for is_block, _ in self.open)

    @property
    def inline_only(self) -> bool:
        return bool(self.open) and not self.block

    def clear(self) -> None:
        self.open.clear()

    def add_line(self, text: str, previous: str, next_article: int) -> bool:
        """Cập nhật theo các dấu ngoặc của dòng. True khi van xả: trích dẫn khối mở quá MAX_QUOTE_LINES dòng."""
        normalized = _same_quotes(text)
        for index, opening in _quote_marks(text):
            if opening:
                self.open.append([_opens_block(normalized, index, previous, next_article), 0])
            elif self.open:
                self.open.pop()
        for entry in self.open:
            entry[1] += 1
        self.open = [entry for entry in self.open if entry[0] or entry[1] <= INLINE_QUOTE_LINES]
        if any(is_block and lines > MAX_QUOTE_LINES for is_block, lines in self.open):
            self.open.clear()
            return True
        return False


def _continues_structure(structure: str, articles: list, clause, point) -> bool:
    """Dòng là ký hiệu cấu trúc kế tiếp: Điều trong khoảng cho phép, đúng Khoản kế tiếp, hoặc đúng Điểm kế tiếp."""
    if heading := ARTICLE.match(structure):
        last = articles[-1].number if articles else 0
        return last < int(heading.group(1)) <= last + ARTICLE_GAP
    if not articles:
        return False
    if clause_match := CLAUSE.match(structure):
        return int(clause_match.group(1)) == (clause.number + 1 if clause else 1)
    if point_match := POINT.match(structure):
        return clause is not None and point_match.group(1) == _next_letter(point.number if point else None)
    return False


def _upper_rest(rest: str) -> bool:
    """Phần sau "Chương II" / "Mục 1" chỉ là tên in hoa (hoặc trống), không phải câu văn nhắc tới Chương."""
    return not any(ch.islower() for ch in rest)


def _next_letter(letter: str | None) -> str | None:
    if letter is None:
        return LETTERS[0]
    index = LETTERS.find(letter)
    return LETTERS[index + 1] if 0 <= index < len(LETTERS) - 1 else None


def _parse_structure(pages_text: list[str]) -> tuple[list[_Node], list[str], list[str]]:
    articles: list[_Node] = []
    front: list[str] = []
    every_line: list[str] = []
    article = clause = point = None
    nested_number: int | None = None  # số tiếp theo của danh sách đánh số lồng (bắt đầu lại từ "1." giữa chừng)
    nested_letter: str | None = None  # chữ tiếp theo của liệt kê a), b)... lồng trong một Điểm
    chapter = section = None
    attach = False
    quotes = _Quotes()
    resync = False  # van ngoặc vừa xả: các tiêu đề Điều trong đoạn bị coi là trích dẫn đã lỡ, nhận Điều kế tiếp

    for page, text in page_lines(pages_text):
        previous = every_line[-1] if every_line else ""
        every_line.append(text)
        structure = OCR_NOISE.sub("", text)  # chỉ dùng để nhận dạng cấu trúc; nội dung giữ nguyên chữ OCR
        structure = OCR_POINT.sub(lambda match: OCR_POINT_MISREADS[match.group(1)], structure)
        next_article = articles[-1].number + 1 if articles else 1
        lead = _leading_quote(structure, previous, next_article)
        if lead == "inline":
            structure = LEADING_QUOTE.sub("", structure)
        structure = ARTICLE_JUNK.sub("", structure)
        line_notes = []
        if structure != text:
            line_notes.append(f"dòng '{text[:12]}' có ký tự nhiễu hoặc đọc nhầm ở đầu (OCR), đã tách theo ký hiệu gần nhất")
        quoted = quotes.block or lead == "block"
        if not quoted and quotes.inline_only:
            if _continues_structure(structure, articles, clause, point):
                quotes.clear()  # ngoặc trong câu chưa đóng mà gặp đúng ký hiệu kế tiếp: dấu ngoặc lạ do OCR
                line_notes.append("có dấu mở ngoặc chưa đóng ở phía trên (OCR có thể chèn ngoặc lạ); đã tách theo ký hiệu này")
            else:
                quoted = True

        heading = ARTICLE.match(structure)
        loose = None if heading or quoted else LOOSE_ARTICLE.match(structure)
        if loose and int(loose.group(1)) == next_article and loose.group(2)[:1].isupper():
            heading = loose
            line_notes.append(f"tiêu đề '{text[:12]}' bị OCR đọc lệch, đã nhận là Điều {next_article} theo thứ tự")
        if heading and not quoted:
            number = int(heading.group(1))
            last = articles[-1].number if articles else None
            if last is None or last < number <= last + ARTICLE_GAP or (resync and number > last):
                article = _Node(number, heading.group(2).strip() or f"Điều {number}", chapter, section, page)
                if last is None and number != 1:
                    article.problems.append(f"văn bản bắt đầu từ Điều {number}")
                elif last is not None and number != last + 1:
                    article.problems.append(f"thiếu Điều {last + 1}–{number - 1} (OCR có thể đã làm mất tiêu đề)")
                article.problems += line_notes
                articles.append(article)
                clause = point = nested_number = nested_letter = None
                attach, resync = True, False
                quotes.clear()
                continue

        if not quoted:
            chapter_match, section_match = CHAPTER.match(structure), SECTION.match(structure)
            if chapter_match and _upper_rest(chapter_match.group(2)):
                chapter, section, attach = chapter_match.group(1), None, False
                if article is None:
                    front.append(text)  # để phần tên văn bản dừng ở đây, không lấy tên Chương
                continue
            if section_match and _upper_rest(section_match.group(2)):
                section, attach = section_match.group(1), False
                if article is None:
                    front.append(text)
                continue

        if article is None:
            front.append(text)  # quốc hiệu, số hiệu, tên văn bản, căn cứ: dùng cho siêu dữ liệu
            continue

        if not quoted and CLOSING.match(structure):
            attach = False
            continue
        if not attach:
            continue  # tên Chương/Mục, phần ký ban hành, phụ lục

        if not quoted:
            clause_match, point_match = CLAUSE.match(structure), POINT.match(structure)
            if clause_match:
                number = int(clause_match.group(1))
                last = clause.number if clause else 0
                if nested_number is not None and number == nested_number:
                    # danh sách đánh số lồng bên trong (hàng của bảng biểu mẫu, liệt kê): không phải Khoản
                    nested_number += 1
                    if number in (last + 1, last + 2):
                        (point or clause or article).problems.append(
                            f"dòng '{number}.' có thể là Khoản mới hoặc mục của danh sách lồng; đã coi là danh sách lồng")
                elif number in (last + 1, last + 2):
                    clause = _Node(number)
                    if number != last + 1:
                        clause.problems.append(f"thiếu Khoản {last + 1}")
                    clause.problems += line_notes
                    article.children.append(clause)
                    point, nested_number, nested_letter = None, None, None
                elif number == 1:
                    nested_number = 2  # đánh số lại từ 1 giữa chừng: bắt đầu danh sách lồng
                else:
                    (point or clause or article).problems.append(
                        f"có dòng đánh số '{number}.' không đúng thứ tự, đã coi là chữ tiếp nối")
            elif point_match:
                letter = point_match.group(1)
                if clause is None:
                    article.problems.append(f"có Điểm '{letter})' không thuộc Khoản nào, đã coi là chữ tiếp nối")
                else:
                    expected = _next_letter(point.number if point else None)
                    note = None
                    if nested_letter is not None and (letter == nested_letter or (letter, nested_letter) == ("d", "đ")):
                        nested_letter = _next_letter(nested_letter)
                        if letter in (expected, "d"):
                            (point or clause).problems.append(
                                f"dòng '{letter})' có thể là Điểm mới hoặc mục của danh sách lồng; đã coi là danh sách lồng")
                        letter = None
                    elif letter == "d" and expected == "đ":
                        letter, note = "đ", "in là 'd)' nhưng theo thứ tự phải là 'đ)'"
                    elif letter == "đ" and expected == "d":
                        letter, note = "d", "in là 'đ)' nhưng theo thứ tự phải là 'd)'"
                    elif letter != expected and expected is not None and letter == _next_letter(expected):
                        note = f"thiếu Điểm '{expected})'"
                    elif letter != expected:
                        if letter == "a":
                            nested_letter = "b"  # liệt kê a), b)... lồng trong một Điểm
                        else:
                            (point or clause).problems.append(
                                f"có dòng '{letter})' không đúng thứ tự, đã coi là chữ tiếp nối")
                        letter = None
                    if letter is not None:
                        point = _Node(letter)
                        point.problems += [problem for problem in (note, *line_notes) if problem]
                        clause.children.append(point)
                        nested_number, nested_letter = None, None

        for node in (article, clause, point):
            if node is not None:
                node.lines.append((page, text))

        if quotes.add_line(text, previous, articles[-1].number + 1):
            article.problems.append("ngoặc “ mở quá dài, có thể OCR làm mất ngoặc đóng")
            resync = True
        if not quotes.block and text.endswith("./."):
            attach = False  # công thức kết thúc phần nội dung văn bản

    return articles, front, every_line


def _flag_blank_pages(articles: list[_Node], pages_text: list[str]) -> None:
    """Trang không đọc được chữ nằm giữa một Điều: nội dung Điều đó có thể thiếu."""
    blank = {page for page, text in enumerate(pages_text, start=1) if not (text or "").strip()}
    for index, article in enumerate(articles):
        start = article.heading_page or 0
        end = articles[index + 1].heading_page if index + 1 < len(articles) else max(article.pages)
        missing = sorted(page for page in blank if start < page < (end or 0))
        if missing:
            article.problems.append(f"trang {', '.join(map(str, missing))} không có chữ (chưa OCR được)")


def _date(match: re.Match | None) -> date | None:
    if match is None:
        return None
    day, month, year = (int(group) for group in match.groups() if group is not None)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _type_heading(line: str) -> str | None:
    """Dòng tên loại văn bản in hoa: "NGHỊ ĐỊNH", hoặc kèm tên trên cùng dòng: "LUẬT CÔNG ĐOÀN"."""
    text = " ".join(line.split())
    if not text or text.upper() != text:
        return None
    for heading in sorted(TYPE_HEADINGS, key=len, reverse=True):
        if text == heading or text.startswith(heading + " "):
            return heading
    return None


def from_file_name(document_id: str) -> tuple[str | None, str | None]:
    """(số hiệu, loại văn bản) theo quy ước đặt tên tệp của bộ dữ liệu; (None, None) nếu tên không theo quy ước."""
    match = FILE_NAME_CONVENTION.match(document_id)
    if match is None:
        return None, None
    kind, number, year, issuer = match.groups()
    if kind == "ND":
        return f"{number}/{year}/NĐ-CP", FILE_NAME_TYPES[kind]
    if kind == "L" and issuer and issuer.startswith("QH"):
        return f"{number}/{year}/{issuer}", FILE_NAME_TYPES[kind]
    if kind == "TT" and issuer:
        return f"{number}/{year}/TT-{issuer}", FILE_NAME_TYPES[kind]
    return None, FILE_NAME_TYPES[kind]


def _letter_share(text: str) -> float:
    visible = "".join(text.split())
    return sum(char.isalpha() for char in visible) / len(visible) if visible else 0.0


def _junk_line(line: str) -> bool:
    """Dòng OCR không phải chữ (T87), rr9T0), ^^...) hoặc là chữ của dấu đóng đè lên phần tên."""
    if STAMP.search(line):
        return True
    words = line.split()
    if words and words[0].strip(".,:;'\"|_-") == "ĐẾN" and " ".join(words[1:]).upper() != " ".join(words[1:]):
        return True  # "ĐẾN uy 26/01..." của dấu Công văn đến; "ĐẾN QUY HOẠCH" in hoa là chữ của tên Luật
    return not WORD.search(line) or _letter_share(line) < 0.5


def plausible_title(title: str | None) -> bool:
    """Chuỗi có thể là tên văn bản: chủ yếu là chữ, không phải quốc hiệu/tiêu ngữ, không phải vài ký tự rác OCR."""
    if not title or MOTTO.search(title) or _letter_share(title) < 0.5:
        return False
    words = WORD.findall(title)
    if len(words) >= 2:
        return True
    return len(words) == 1 and len(words[0]) >= 3 and (words[0].isupper() or words[0].istitle())  # Luật DƯỢC


def _clean_title(text: str) -> str:
    return TITLE_TAIL.sub("", " ".join(text.split()))[:400]


def _heading_title(front: list[str], type_index: int, heading: str) -> tuple[str, bool]:
    """Tên văn bản nằm ngay dưới dòng loại văn bản, đến dòng "Căn cứ"; trả thêm cờ "có dòng rác/dấu đóng bị bỏ"."""
    same_line = " ".join(front[type_index].split())[len(heading):].strip()  # "LUẬT CÔNG ĐOÀN"
    lines = [same_line] if same_line else []
    for line in front[type_index + 1:type_index + 1 + MAX_TITLE_LINES]:
        if line.startswith(TITLE_STOPS):
            break
        lines.append(line)
    kept, damaged = [], False
    for line in lines:
        if not kept:
            line = TITLE_LEAD.sub("", line)  # ", “, ”, "| hay số trang OCR đọc lẫn trước chữ đầu tiên
        if _junk_line(line):
            damaged = True
            continue
        kept.append(line)
    return _clean_title(" ".join(kept)), damaged


def _enacted_title(front: list[str]) -> str | None:
    """Tên văn bản trong câu ban hành ("Quốc hội ban hành Luật Quản lý thuế."): nằm ở thân văn bản nên ít bị dấu đóng
    đè lên như phần tên in hoa ở đầu trang."""
    lines = front[-ENACTING_WINDOW:]  # câu ban hành đứng ngay trước Chương I / Điều 1
    matches = list(ENACTING.finditer(" ".join(" ".join(lines).split())))
    if not matches:
        return None
    title = _clean_title(matches[-1].group(1))
    return title[:1].upper() + title[1:]


def read_metadata(front: list[str], every_line: list[str], document_id: str = "") -> Metadata:
    meta = Metadata()
    front_text, all_text = "\n".join(front), "\n".join(every_line)
    file_no, file_type = from_file_name(document_id)
    meta.document_no = next((match.group(1) for match in DOCUMENT_NO.finditer(front_text)
                             if VALID_DOCUMENT_NO.match(match.group(1))), None)
    if meta.document_no is None and not BLANK_DOCUMENT_NO.search(front_text):
        meta.document_no = file_no  # có số nhưng OCR đọc hỏng (4452020/NĐ-CP, 2£5/2020) hoặc mất dòng số hiệu

    type_index, heading = next(((i, h) for i, line in enumerate(front) if (h := _type_heading(line))), (None, None))
    header_text = front_text
    heading_title, damaged = None, False
    if type_index is not None:
        meta.document_type = TYPE_HEADINGS[heading]
        header_text = "\n".join(front[:type_index])
        heading_title, damaged = _heading_title(front, type_index, heading)
    elif meta.document_no:
        suffix = meta.document_no.upper()
        meta.document_type = ("Nghị định" if "NĐ-CP" in suffix else "Thông tư" if "TT-" in suffix
                              else "Quyết định" if "QĐ-" in suffix else None)
    meta.document_type = meta.document_type or file_type

    # tên dưới dòng loại văn bản; bị dấu đóng/rác OCR làm hỏng thì lấy câu ban hành, đủ chữ hơn
    candidates = (None if damaged else heading_title, _enacted_title(front), heading_title)
    meta.title = next((title for title in candidates if plausible_title(title)), None)
    if meta.title is None and type_index is None and front and plausible_title(first := _clean_title(front[0])):
        meta.title = first[:180]
    meta.issued_date = _date(ISSUED_IN_HEADER.search(header_text)) or _date(ISSUED_BY_VOTE.search(all_text))
    meta.effective_from = _date(EFFECTIVE.search(all_text))
    return meta


def parse_legal_document(pages_text: list[str], document_id: str) -> ParsedDocument:
    articles, front, every_line = _parse_structure(pages_text)
    _flag_blank_pages(articles, pages_text)

    units: list[LegalUnit] = []

    def unit(node: _Node, article: _Node, clause: int | None, point: str | None, content: str) -> LegalUnit:
        return LegalUnit(
            unit_id=make_unit_id(document_id, article.number, clause, point),
            chapter=article.chapter,
            section=article.section,
            article=article.number,
            clause=clause,
            point=point,
            title=article.title,
            content=content,
            is_confident=not node.problems,
            source_pages=node.pages,
        )

    for article in articles:
        units.append(unit(article, article, None, None, article.content or article.title))
        for clause in article.children:
            units.append(unit(clause, article, clause.number, None, clause.content))
            for point in clause.children:
                units.append(unit(point, article, clause.number, point.number, point.content))
    return ParsedDocument(units=units, metadata=read_metadata(front, every_line, document_id),
                          has_text=any((text or "").strip() for text in pages_text))
