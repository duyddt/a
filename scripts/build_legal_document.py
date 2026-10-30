"""Dựng document.json (LegalDocument) từ bản chép chữ từng trang của PDF scan.

Đầu vào : thư mục có p001.txt ... pNNN.txt (dòng 1 `PRINTED_PAGE: ...`, dòng 2 trống, sau đó mỗi đoạn một dòng).
Đầu ra  : document.json theo định dạng của app (xem docs/input-format.md), kèm báo cáo bất thường.

    python scripts/build_legal_document.py --pages-dir data/ND254_2026/ocr_pages \
        --out data/ND254_2026/document.json

Nguyên tắc: không đoán. Chỗ nào số thứ tự không khớp (nhảy cóc, lặp, sai ký hiệu) thì script ghi vào báo cáo và
đặt is_confident=false cho đơn vị đó, KHÔNG tự sửa nội dung. Số hiệu, ngày ban hành, ngày hiệu lực ở dưới lấy
từ chính văn bản và CHƯA được đối chiếu với nguồn chính thức.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.contracts import LegalDocument, make_document_id, make_unit_id  # noqa: E402
from app.services.store import check_source_pages  # noqa: E402

# ---- Thông tin văn bản (đọc từ trang 1 và Điều 43 khoản 1; chưa xác minh với nguồn chính thức) ----
DOCUMENT_TYPE = "Nghị định"
DOCUMENT_NO = "254/2026/NĐ-CP"
TITLE = (
    "Quy định chi tiết một số điều và biện pháp để tổ chức, hướng dẫn thi hành Luật Quản lý thuế "
    "số 108/2025/QH15 về hóa đơn điện tử, chứng từ điện tử"
)
ISSUER = "Chính phủ"
ISSUED_DATE = "2026-06-30"
EFFECTIVE_FROM = "2026-07-01"
BODY_PAGES = (1, 47)  # từ trang đầu đến hết "Nơi nhận" và chữ ký
APPENDIX_PAGES = (48, 55)
APPENDIX_UNIT_SUFFIX = "PL1"

# Thứ tự ký hiệu Điểm trong văn bản pháp luật Việt Nam (không có f, j, w, z)
LETTERS = list("abcdđeghiklmnopqrstuvxy")

RE_CHAPTER = re.compile(r"^Chương\s+([IVXLCDM]+)\s*$")
RE_SECTION = re.compile(r"^Mục\s+(\d+)\s*$")
RE_ARTICLE = re.compile(r"^Điều\s+(\d+)\.\s+(.+)$")
RE_CLAUSE = re.compile(r"^(\d+)\.\s+\S")
RE_POINT = re.compile(r"^([a-zđ])\)\s+\S")
RE_SUBPOINT = re.compile(r"^[a-zđ]\.\d")  # a.1), b.2.1): thuộc nội dung của Điểm, không tách đơn vị riêng


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
                previous = paras[-1]
                starts_new = RE_POINT.match(text) or RE_SUBPOINT.match(text)
                if previous.text[-1] not in ".;:" and text[0].islower() and not starts_new:
                    previous.text += " " + text
                    previous.pages.add(page)
                    continue
            paras.append(Para(text, {page}))
    return paras


def parse_body(paras: list[Para], problems: list[str]) -> list[Article]:
    articles: list[Article] = []
    chapter: str | None = None
    section: str | None = None
    skip_title = 0
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
        if skip_title:
            skip_title -= 1
            continue
        if m := RE_CHAPTER.match(text):
            chapter, section, skip_title = m.group(1), None, 1
            article = clause = point = None
            continue
        if m := RE_SECTION.match(text):
            section, skip_title = m.group(1), 1
            article = clause = point = None
            continue
        if (m := RE_ARTICLE.match(text)) and int(m.group(1)) == len(articles) + 1:
            article = Article(number=int(m.group(1)), title=m.group(2).strip(), chapter=chapter, section=section)
            article.paras.append(Para(text, set(para.pages)))  # dòng tiêu đề: chỉ để tính trang, bỏ khỏi content
            article.paras.pop()
            article.heading_pages = set(para.pages)  # type: ignore[attr-defined]
            articles.append(article)
            clause = point = None
            continue
        if article is None:
            continue  # phần mở đầu trước Điều 1 (quốc hiệu, căn cứ...), không thuộc đơn vị nào

        if m := RE_CLAUSE.match(text):
            number = int(m.group(1))
            if number == len(article.clauses) + 1:
                clause = Clause(number=number)
                article.clauses.append(clause)
                point = None
                keep(para)
                continue
            problems.append(f"Điều {article.number}: dòng bắt đầu bằng '{number}.' nhưng cần Khoản {len(article.clauses) + 1}; coi là chữ tiếp nối: {text[:60]!r}")
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

        keep(para)  # đoạn tiếp nối (không có ký hiệu), gồm cả a.1), a.2.1)

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
        # Trang của Điều gồm cả dòng tiêu đề (đã bỏ khỏi content)
        article_pages = sorted(set(article.pages) | getattr(article, "heading_pages", set()))
        unit = base(article, article, make_unit_id(document_id, article.number), None, None)
        unit["source_pages"] = article_pages
        units.append(unit)
        for clause in article.clauses:
            units.append(base(article, clause, make_unit_id(document_id, article.number, clause.number), clause.number, None))
            for point in clause.points:
                units.append(base(article, point, make_unit_id(document_id, article.number, clause.number, point.letter), clause.number, point.letter))
    return units


def build_appendix(document_id: str, paras: list[Para]) -> dict:
    """Phụ lục: chưa tách cấu trúc (ngoài phạm vi Điều/Khoản/Điểm), giữ nguyên thành MỘT đơn vị để người soát xem."""
    head = [p.text for p in paras[:3]]
    if not (head[0].lower() == "phụ lục" and head[2].startswith("(Kèm theo")):
        raise SystemExit(f"Đầu phụ lục không như mong đợi: {head!r}")
    title = head[1]
    body = paras[3:]
    pages = sorted(set().union(*(p.pages for p in paras)))
    return {
        "unit_id": f"{document_id}_{APPENDIX_UNIT_SUFFIX}",
        "chapter": None,
        "section": None,
        "article": None,
        "clause": None,
        "point": None,
        "title": f"Phụ lục: {title}",
        "content": "\n".join(p.text for p in body),
        "effective_from": EFFECTIVE_FROM,
        "effective_to": None,
        "status": "active",
        "is_confident": False,  # chưa tách cấu trúc phụ lục
        "source_pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pages-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    document_id = make_document_id(DOCUMENT_TYPE, DOCUMENT_NO)
    problems: list[str] = []

    body = read_paragraphs(args.pages_dir, *BODY_PAGES)
    appendix = read_paragraphs(args.pages_dir, *APPENDIX_PAGES)
    articles = parse_body(body, problems)

    units = build_units(document_id, articles)
    units.append(build_appendix(document_id, appendix))

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

    # Kiểm tra bằng đúng model của app, và kiểm tra source_pages theo số trang thực tế
    parsed = LegalDocument.model_validate(document)
    page_count = APPENDIX_PAGES[1]
    page_problems = check_source_pages(parsed, page_count)
    if page_problems:
        raise SystemExit("Lỗi source_pages:\n" + "\n".join(page_problems))
    leftovers = [u["unit_id"] for u in units if "[?]" in u["content"] or "[không đọc được]" in u["content"]]
    if leftovers:
        problems.append(f"Còn dấu chưa chắc từ bước chép chữ trong: {leftovers}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    clauses = sum(1 for u in units if u["clause"] is not None and u["point"] is None)
    points = sum(1 for u in units if u["point"] is not None)
    flagged = [u["unit_id"] for u in units if not u["is_confident"]]
    print(f"Đã ghi {args.out}")
    print(f"Điều: {len(articles)}  Khoản: {clauses}  Điểm: {points}  Phụ lục: 1  Tổng đơn vị: {len(units)}")
    print(f"Đơn vị is_confident=false: {len(flagged)}")
    print(f"Bất thường khi tách: {len(problems)}")
    for line in problems:
        print("  -", line)


if __name__ == "__main__":
    main()
