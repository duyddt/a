"""Tạo ocr_pages/ và document.json cho ND255_2026 tương tự cấu trúc ND254_2026."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pypdfium2 as pdfium
from app.contracts import LegalDocument, make_document_id, make_unit_id
from app.services.store import check_source_pages

DOCUMENT_TYPE = "Nghị định"
DOCUMENT_NO = "255/2026/NĐ-CP"
TITLE = "Quy định về quản lý thuế đối với các giao dịch liên kết của những doanh nghiệp có quan hệ liên kết"
ISSUER = "Chính phủ"
ISSUED_DATE = "2026-06-30"
EFFECTIVE_FROM = "2026-07-01"
PAGE_COUNT = 68

LETTERS = list("abcdđeghiklmnopqrstuvxy")

RE_CHAPTER = re.compile(r"^Chương\s+([IVXLCDM]+)\s*$")
RE_SECTION = re.compile(r"^Mục\s+(\d+)\s*$")
RE_ARTICLE = re.compile(r"^Điều\s+(\d+)\.\s*(.+)$")
RE_CLAUSE = re.compile(r"^(\d+)\.\s+\S")
RE_POINT = re.compile(r"^([a-zđ])\)\s+\S")
RE_SUBPOINT = re.compile(r"^[a-zđ]\.\d")


@dataclass
class Para:
    text: str
    pages: set[int]


@dataclass
class Node:
    paras: list[Para] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def add(self, para: Para) -> None:
        self.paras.append(para)

    @property
    def content(self) -> str:
        return "\n".join(p.text for p in self.paras)

    @property
    def pages(self) -> list[int]:
        return sorted(set().union(*(p.pages for p in self.paras))) if self.paras else []


@dataclass
class Point(Node):
    letter: str = ""


@dataclass
class Clause(Node):
    number: int = 0
    points: list[Point] = field(default_factory=list)


@dataclass
class Article(Node):
    number: int = 0
    title: str = ""
    chapter: str | None = None
    section: str | None = None
    clauses: list[Clause] = field(default_factory=list)


def extract_page_lines_from_pdf(page_text: str) -> list[str]:
    """Tách và ghép dòng từ PDF thành danh sách đoạn văn hoàn chỉnh trên một trang."""
    raw_lines = [l.strip() for l in page_text.splitlines() if l.strip() and not re.match(r"^-{3,}$", l.strip())]
    paras: list[str] = []
    curr: list[str] = []

    def flush():
        if curr:
            paras.append(" ".join(curr))
            curr.clear()

    idx = 0
    while idx < len(raw_lines):
        line = raw_lines[idx]

        if re.match(r"^(CHÍNH PHỦ|CỘNG HÒA|Độc lập|Số:|Hà Nội,|NGHỊ ĐỊNH|Căn cứ|Theo đề nghị|Chính phủ ban hành)", line):
            flush()
            curr.append(line)
        elif re.match(r"^Chương\s+[IVXLCDM]+", line):
            flush()
            paras.append(line)
        elif re.match(r"^Điều\s+\d+\.", line):
            flush()
            # Ghép dòng tiếp theo nếu là phần tiêu đề bị ngắt dòng (bắt đầu bằng chữ thường)
            full_article_heading = line
            if idx + 1 < len(raw_lines):
                nxt = raw_lines[idx + 1]
                if nxt and nxt[0].islower() and not re.match(r"^([a-zđ]\)|[a-zđ]\.\d|\d+\.)", nxt):
                    full_article_heading += " " + nxt
                    idx += 1
            paras.append(full_article_heading)
        elif re.match(r"^[A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ\s,;]+$", line) and len(line) > 5 and not curr:
            flush()
            paras.append(line)
        elif re.match(r"^\d+\.\s+\S", line):
            flush()
            curr.append(line)
        elif re.match(r"^[a-zđ]\)\s+\S", line):
            flush()
            curr.append(line)
        elif re.match(r"^[a-zđ]\.\d", line):
            flush()
            curr.append(line)
        elif re.match(r"^[-+•*]\s+", line):
            flush()
            curr.append(line)
        elif re.match(r"^(PHỤ LỤC|Phụ lục)\s+([IVXLCDM]+|\d+)", line):
            flush()
            paras.append(line)
        elif re.match(r"^\(Kèm theo", line):
            flush()
            curr.append(line)
        elif re.match(r"^(Nơi nhận:|TM\.|KT\.|THỦ TƯỚNG|PHÓ THỦ TƯỚNG|Nguyễn Văn Thắng)", line):
            flush()
            curr.append(line)
        elif curr and curr[-1].endswith((".", ":", ";", ".”")) and line[0].isupper() and not line.startswith("Nghị định này"):
            flush()
            curr.append(line)
        else:
            curr.append(line)
        idx += 1
    flush()
    return paras


def generate_ocr_pages(pdf_path: Path, ocr_dir: Path) -> None:
    """Tạo các file p001.txt..p068.txt từ PDF."""
    ocr_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    print(f"Trích xuất {len(pdf)} trang vào {ocr_dir}...")
    for page_idx in range(len(pdf)):
        page_num = page_idx + 1
        page_text = pdf[page_idx].get_textpage().get_text_range()
        lines = extract_page_lines_from_pdf(page_text)

        printed_page = "none" if page_num == 1 else str(page_num)
        content = f"PRINTED_PAGE: {printed_page}\n\n" + "\n\n".join(lines) + "\n"
        out_file = ocr_dir / f"p{page_num:03d}.txt"
        out_file.write_text(content, encoding="utf-8")
    print(f"Đã tạo {len(pdf)} file trang txt thành công.")


def read_paragraphs(pages_dir: Path, first: int, last: int) -> list[Para]:
    """Đọc các trang first..last thành danh sách đoạn; nối đoạn bị ngắt giữa hai trang."""
    paras: list[Para] = []
    for page in range(first, last + 1):
        path = pages_dir / f"p{page:03d}.txt"
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()[2:] if line.strip()]
        if lines == ["[TRANG TRỐNG]"]:
            continue
        for index, text in enumerate(lines):
            if index == 0 and paras:
                prev = paras[-1]
                starts_new = (
                    RE_POINT.match(text)
                    or RE_SUBPOINT.match(text)
                    or RE_ARTICLE.match(text)
                    or RE_CLAUSE.match(text)
                    or RE_CHAPTER.match(text)
                    or text.startswith("PHỤ LỤC")
                    or text.startswith("Nơi nhận")
                )
                if prev.text[-1] not in ".;:" and not starts_new:
                    prev.text += " " + text
                    prev.pages.add(page)
                    continue
            paras.append(Para(text, {page}))
    return paras


def parse_body(paras: list[Para], problems: list[str]) -> list[Article]:
    articles: list[Article] = []
    chapter: str | None = None
    section: str | None = None
    skip_title = False
    article: Article | None = None
    clause: Clause | None = None
    point: Point | None = None

    def keep(para: Para) -> None:
        assert article is not None
        article.add(para)
        if clause is not None:
            clause.add(para)
        if point is not None:
            point.add(para)

    for para in paras:
        text = para.text
        if text.startswith("Nơi nhận"):
            break
        if m := RE_CHAPTER.match(text):
            chapter, section = m.group(1), None
            skip_title = True
            article = clause = point = None
            continue
        if m := RE_SECTION.match(text):
            section = m.group(1)
            skip_title = True
            article = clause = point = None
            continue
        if (m := RE_ARTICLE.match(text)) and int(m.group(1)) == len(articles) + 1:
            skip_title = False
            article = Article(number=int(m.group(1)), title=m.group(2).strip(), chapter=chapter, section=section)
            article.paras.append(Para(text, set(para.pages)))
            article.paras.pop()
            article.heading_pages = set(para.pages)  # type: ignore[attr-defined]
            articles.append(article)
            clause = point = None
            continue
        if skip_title:
            if text.isupper() or len(text) < 120:
                continue
            skip_title = False
        if article is None:
            continue

        if m := RE_CLAUSE.match(text):
            number = int(m.group(1))
            if number == len(article.clauses) + 1:
                clause = Clause(number=number)
                article.clauses.append(clause)
                point = None
                keep(para)
                continue
            problems.append(
                f"Điều {article.number}: dòng bắt đầu bằng '{number}.' nhưng cần Khoản {len(article.clauses) + 1}; coi là chữ tiếp nối: {text[:60]!r}"
            )
            (clause or article).problems.append(f"có dòng đánh số '{number}.' không đúng thứ tự, đã coi là nội dung tiếp nối")
            keep(para)
            continue

        if m := RE_POINT.match(text):
            letter = m.group(1)
            if clause is None:
                problems.append(f"Điều {article.number}: Điểm '{letter})' xuất hiện khi chưa có Khoản; coi là chữ tiếp nối: {text[:60]!r}")
                article.problems.append(f"có Điểm '{letter})' không thuộc Khoản nào, đã coi là nội dung tiếp nối")
                keep(para)
                continue
            expected = LETTERS[len(clause.points)] if len(clause.points) < len(LETTERS) else None
            note = None
            if letter == expected:
                pass
            elif letter == "d" and expected == "đ":
                letter, note = "đ", "in là 'd)' nhưng theo thứ tự phải là 'đ)'; đã ghi là Điểm đ"
            elif letter in LETTERS and expected in LETTERS and LETTERS.index(letter) > LETTERS.index(expected):
                note = f"Điểm '{letter})' đứng sau Điểm '{LETTERS[len(clause.points) - 1] if clause.points else '(không có)'})': thiếu ký hiệu ở giữa"
            else:
                problems.append(f"Điều {article.number} khoản {clause.number}: Điểm '{letter})' sai thứ tự (cần '{expected})'); coi là chữ tiếp nối: {text[:60]!r}")
                (point or clause).problems.append(f"có dòng '{letter})' không đúng thứ tự, đã coi là nội dung tiếp nối")
                keep(para)
                continue
            point = Point(letter=letter)
            if note:
                point.problems.append(note)
                problems.append(f"Điều {article.number} khoản {clause.number} điểm {letter}: {note}")
            clause.points.append(point)
            keep(para)
            continue

        keep(para)

    return articles


def build_units(document_id: str, articles: list[Article]) -> list[dict]:
    units: list[dict] = []

    def base(article: Article, node: Node, unit_id: str, clause: int | None, point: str | None) -> dict:
        return {
            "unit_id": unit_id,
            "chapter": article.chapter,
            "section": article.section,
            "article": article.number,
            "clause": clause,
            "point": point,
            "title": article.title,
            "content": node.content,
            "effective_from": EFFECTIVE_FROM,
            "effective_to": None,
            "status": "active",
            "is_confident": not node.problems,
            "source_pages": node.pages,
        }

    for article in articles:
        article_pages = sorted(set(article.pages) | getattr(article, "heading_pages", set()))
        unit = base(article, article, make_unit_id(document_id, article.number), None, None)
        unit["source_pages"] = article_pages
        units.append(unit)
        for clause in article.clauses:
            units.append(base(article, clause, make_unit_id(document_id, article.number, clause.number), clause.number, None))
            for point in clause.points:
                units.append(base(article, point, make_unit_id(document_id, article.number, clause.number, point.letter), clause.number, point.letter))
    return units


def build_appendix_units(document_id: str, pages_dir: Path) -> list[dict]:
    """Tách 5 phụ lục thành 5 đơn vị riêng biệt."""
    all_paras = read_paragraphs(pages_dir, 31, 68)

    pl_markers = [
        ("PL1", "PHỤ LỤC I", "I", "THÔNG TIN VỀ QUAN HỆ LIÊN KẾT VÀ GIAO DỊCH LIÊN KẾT"),
        ("PL2", "PHỤ LỤC II", "II", "DANH MỤC CÁC THÔNG TIN, TÀI LIỆU CẦN CUNG CẤP TẠI HỒ SƠ QUỐC GIA"),
        ("PL3", "PHỤ LỤC III", "III", "DANH MỤC CÁC THÔNG TIN, TÀI LIỆU CẦN CUNG CẤP TẠI HỒ SƠ TOÀN CẦU"),
        ("PL4", "PHỤ LỤC IV", "IV", "KÊ KHAI THÔNG TIN BÁO CÁO LỢI NHUẬN LIÊN QUỐC GIA"),
        ("PL5", "PHỤ LỤC V", "V", "CÔNG THỨC TÍNH GIÁ TRỊ GIAO DỊCH ĐỘC LẬP CHUẨN"),
    ]

    pl_indices: list[int] = []
    for suffix, header, roman, title in pl_markers:
        found = False
        for i, p in enumerate(all_paras):
            if p.text.strip() == header:
                pl_indices.append(i)
                found = True
                break
        if not found:
            raise ValueError(f"Không tìm thấy {header} trong all_paras")

    pl_indices.append(len(all_paras))

    units: list[dict] = []
    for idx, (suffix, header, roman, title) in enumerate(pl_markers):
        start = pl_indices[idx]
        end = pl_indices[idx + 1]
        pl_paras = all_paras[start:end]

        body_paras = pl_paras[3:] if len(pl_paras) > 3 else pl_paras
        pages = sorted(set().union(*(p.pages for p in pl_paras)))

        units.append({
            "unit_id": f"{document_id}_{suffix}",
            "chapter": None,
            "section": None,
            "article": None,
            "clause": None,
            "point": None,
            "title": f"Phụ lục {roman}: {title}",
            "content": "\n".join(p.text for p in body_paras),
            "effective_from": EFFECTIVE_FROM,
            "effective_to": None,
            "status": "active",
            "is_confident": False,
            "source_pages": pages,
        })
    return units


def build_nd255(pages_dir: Path, out_path: Path) -> None:
    document_id = make_document_id(DOCUMENT_TYPE, DOCUMENT_NO)
    problems: list[str] = []

    body_paras = read_paragraphs(pages_dir, 1, 31)
    articles = parse_body(body_paras, problems)

    units = build_units(document_id, articles)
    pl_units = build_appendix_units(document_id, pages_dir)
    units.extend(pl_units)

    document = {
        "document_id": document_id,
        "document_no": DOCUMENT_NO,
        "document_type": DOCUMENT_TYPE,
        "title": TITLE,
        "issuer": ISSUER,
        "issued_date": ISSUED_DATE,
        "effective_from": EFFECTIVE_FROM,
        "effective_to": None,
        "status": "active",
        "amended_by": [],
        "replaced_by": None,
        "legal_units": units,
    }

    parsed = LegalDocument.model_validate(document)
    page_problems = check_source_pages(parsed, PAGE_COUNT)
    if page_problems:
        raise SystemExit("Lỗi source_pages:\n" + "\n".join(page_problems))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    clauses = sum(1 for u in units if u["clause"] is not None and u["point"] is None)
    points = sum(1 for u in units if u["point"] is not None)
    flagged = [u["unit_id"] for u in units if not u["is_confident"]]
    print(f"Đã ghi {out_path}")
    print(f"Điều: {len(articles)}  Khoản: {clauses}  Điểm: {points}  Phụ lục: {len(pl_units)}  Tổng đơn vị: {len(units)}")
    print(f"Đơn vị is_confident=false: {len(flagged)}")
    print(f"Bất thường khi tách: {len(problems)}")
    for line in problems:
        print("  -", line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages-dir", type=Path, default=ROOT / "data" / "ND255_2026" / "ocr_pages")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "ND255_2026" / "document.json")
    parser.add_argument("--extract-pdf", action="store_true", help="Trích xuất lại ocr_pages từ source.pdf")
    args = parser.parse_args()

    doc_dir = ROOT / "data" / "ND255_2026"
    raw_pdf = doc_dir / "255_2026_ND-CP_713416.pdf"
    source_pdf = doc_dir / "source.pdf"

    if not source_pdf.is_file() and raw_pdf.is_file():
        shutil.copy2(raw_pdf, source_pdf)
        print(f"Đã sao chép sang {source_pdf}")

    if args.extract_pdf or not (args.pages_dir / "p001.txt").is_file():
        generate_ocr_pages(source_pdf, args.pages_dir)

    build_nd255(args.pages_dir, args.out)


if __name__ == "__main__":
    main()
