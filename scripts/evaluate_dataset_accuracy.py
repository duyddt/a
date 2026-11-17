"""Đo độ chính xác của luồng nhập PDF (auto_build_json_from_pdf) trên bộ dữ liệu, bằng chính công thức của app.

    PYTHONUTF8=1 python scripts/evaluate_dataset_accuracy.py "bo du lieu" --seeds data --out eval.json

Cách làm (chi tiết và giới hạn: docs/do-chinh-xac-ocr.md, mục 7; kết quả: docs/bao-cao-kiem-thu-bo-du-lieu.md).
Có hai nguồn Ground Truth:
- (A) Văn bản mẫu trong --seeds (data/<ID>/ocr_pages + document.json do nhóm dựng và soát bằng công cụ khác):
  chạy bộ tách của app trên bản chép chữ từng trang rồi so với document.json. Đây là phép đo ĐỘC LẬP.
- (B) PDF có lớp chữ (>= 80% số trang có chữ) trong bộ dữ liệu: bộ tách tham chiếu bên dưới đọc lớp chữ,
  nối nội dung qua ranh giới trang, bỏ dòng tiêu đề trang Công báo, chỉ nhận Điều/Khoản/Điểm đánh số liên tiếp,
  ngoài phần trích dẫn “…”, và dừng ở phần ký ban hành/phụ lục. Bộ tách của app dùng các quy tắc tương tự nên (B)
  chủ yếu là phép kiểm tra hồi quy, không phải bằng chứng độc lập. Cả hai CHƯA phải bản được con người duyệt.
- Đầu ra hệ thống = đúng hàm app dùng khi người dùng chỉ tải lên PDF (auto_build_json_from_pdf).
- Ghép đơn vị theo khóa (Điều, Khoản, Điểm); Điều chỉ được ghép khi tiêu đề trùng (>= 60% giống) để bản bị nhận nhầm
  sang văn bản khác không được tính là "khớp".
- Đơn vị khớp -> giả lập "approved" (Ground Truth = nội dung tham chiếu, original_content = đầu ra hệ thống);
  đơn vị hệ thống tách thừa -> "structure_error"; rồi gọi app.services.review.build_ocr_report.
  Đơn vị tham chiếu mà hệ thống bỏ sót không hiện trong app nên không vào SFI của app; báo riêng ở "recall".
- Khoảng trắng (xuống dòng do dàn trang PDF) được gộp về một dấu cách ở cả hai phía trước khi so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pypdfium2 as pdfium  # noqa: E402
from app.services import review  # noqa: E402
from app.services.models import ImportProblem  # noqa: E402
from app.services.pdf_parser import (  # noqa: E402
    auto_build_json_from_pdf,
    build_document_from_pages,
    extract_pages_text,
)

# ---------------------------------------------------------------- bộ tách tham chiếu (Ground Truth) cho PDF có lớp chữ
HEADER = re.compile(r"^\s*(\d{1,4}\s+)?CÔNG BÁO\s*/.*$")
PAGE_NUMBER = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$")
ARTICLE = re.compile(r"^\s*Điều\s+(\d+)\s*\.\s*(.*)$")
CLAUSE = re.compile(r"^\s*(\d+)\.\s+\S")
POINT = re.compile(r"^\s*([a-zđ])\)\s+\S")
CHAPTER = re.compile(r"^\s*(Chương\s+[IVXLC]+|Mục\s+\d+)\b")
CLOSING = re.compile(
    r"^\s*(Nơi nhận\s*:|CHỦ TỊCH QUỐC HỘI|TM\.\s|KT\.\s|BỘ TRƯỞNG|THỦ TƯỚNG|PHỤ LỤC|Phụ lục\s+[IVX\d]|"
    r"Luật này (đã )?được Quốc hội)"  # biểu mẫu ("Mẫu số ...") có thể nằm ngay trong thân Điều: không dừng ở đó
)
LETTERS = "abcdđeghiklmnopqrstuvxy"
END_PUNCT = (".", ";", ":", "”", '"', "!", "?")


class RefUnit:
    def __init__(self, key: tuple, title: str):
        self.key, self.title, self.lines = key, title, []

    @property
    def content(self) -> str:
        return "\n".join(text for _, text in self.lines).strip()

    @property
    def pages(self) -> list[int]:
        return sorted({page for page, text in self.lines if text.strip()})


def page_lines(pdf_path: Path):
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(pdf)):
            textpage = pdf[index].get_textpage()
            raw = (textpage.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n")
            textpage.close()
            lines = raw.split("\n")
            filled = [i for i, line in enumerate(lines) if line.strip()]
            edges = {filled[0], filled[-1]} if filled else set()
            for i, line in enumerate(lines):
                # số trang chỉ nằm ở mép trang; số đứng riêng giữa trang là ô của bảng, phải giữ
                if not HEADER.match(line) and not (i in edges and PAGE_NUMBER.match(line)):
                    yield index + 1, line
    finally:
        pdf.close()


def quote_depth_after(depth: int, text: str) -> int:
    """“ mở, ” đóng; dấu " thẳng đóng nếu đang mở, không thì mở (văn bản hay lẫn “…" khi soạn)."""
    for char in text:
        if char == "“":
            depth += 1
        elif char == "”":
            depth = max(0, depth - 1)
        elif char == '"':
            depth = depth - 1 if depth > 0 else depth + 1
    return depth


def parse_reference(pdf_path: Path) -> list[RefUnit]:
    """Danh sách đánh số lại từ "1." trong một Khoản (hàng của biểu mẫu) và liệt kê "a)" lại trong một Điểm
    là danh sách lồng: số/chữ tiếp theo của danh sách lồng được ưu tiên hơn Khoản/Điểm kế tiếp."""
    units: list[RefUnit] = []
    article = clause = point = None
    nested_number = nested_letter = None
    next_article, quote_depth, prev_text, finished = 1, 0, "", False
    for page, line in page_lines(pdf_path):
        text = line.strip()
        if not text:
            for unit in (article, clause, point):
                if unit is not None:
                    unit.lines.append((page, line))
            continue
        quoted = quote_depth > 0 or text.startswith(("“", '"'))
        boundary = not prev_text or prev_text.endswith(END_PUNCT) or ARTICLE.match(prev_text) is not None
        if article is not None and clause is None and not any(t.strip().endswith(END_PUNCT) for _, t in article.lines):
            boundary = True  # vẫn đang ở tiêu đề Điều xuống dòng
        m_article = ARTICLE.match(text)
        if not quoted and m_article and int(m_article.group(1)) == next_article:
            article = RefUnit((int(m_article.group(1)), None, None), m_article.group(2).strip())
            units.append(article)
            clause = point = nested_number = nested_letter = None
            next_article += 1
            finished, quote_depth, prev_text = False, 0, text
            continue
        if article is None or finished:
            prev_text = text
            continue
        if not quoted and boundary and (CHAPTER.match(text) or CLOSING.match(text)):
            finished, prev_text = True, text  # sang Chương/Mục mới hoặc phần ký ban hành, phụ lục
            continue
        m_clause, m_point = CLAUSE.match(text), POINT.match(text)
        expected_clause = clause.key[1] + 1 if clause else 1
        if not quoted and m_clause:
            number = int(m_clause.group(1))
            if number == nested_number:
                nested_number += 1
            elif number == expected_clause:
                clause = RefUnit((article.key[0], expected_clause, None), article.title)
                units.append(clause)
                point = nested_number = nested_letter = None
            elif number == 1 and clause:
                nested_number = 2
        elif not quoted and clause and m_point:
            letter = m_point.group(1)
            expected_point = LETTERS[LETTERS.index(point.key[2]) + 1] if point and point.key[2] in LETTERS[:-1] else "a"
            if letter == nested_letter:
                nested_letter = LETTERS[LETTERS.index(letter) + 1] if letter in LETTERS[:-1] else None
            elif letter == expected_point:
                point = RefUnit((article.key[0], clause.key[1], expected_point), article.title)
                units.append(point)
                nested_number = nested_letter = None
            elif letter == "a" and point:
                nested_letter = "b"
        for unit in (article, clause, point):
            if unit is not None:
                unit.lines.append((page, line))
        quote_depth = quote_depth_after(quote_depth, text)
        prev_text = text
    return units


# ---------------------------------------------------------------- so khớp và tính chỉ số
def squash(text: str) -> str:
    return " ".join(text.split())


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, squash(a).lower(), squash(b).lower(), autojunk=False).ratio()



def text_page_ratio(pdf_path: Path) -> tuple[int, int]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        with_text = 0
        for index in range(len(pdf)):
            textpage = pdf[index].get_textpage()
            with_text += len((textpage.get_text_range() or "").strip()) >= 15
            textpage.close()
        return with_text, len(pdf)
    finally:
        pdf.close()


def unit_key(unit: dict) -> tuple | None:
    if unit.get("article") is None:
        return None
    return unit["article"], unit.get("clause"), unit.get("point")


def seed_reference(document: dict) -> list[SimpleNamespace]:
    """Đơn vị Điều/Khoản/Điểm của một document.json đã soát (bỏ phụ lục, không có số Điều)."""
    return [
        SimpleNamespace(key=unit_key(unit), title=unit.get("title") or "", content=unit["content"],
                        pages=unit.get("source_pages", []))
        for unit in document["legal_units"] if unit_key(unit) is not None
    ]


def seed_pages(ocr_dir: Path) -> list[str]:
    """Bản chép chữ từng trang (p001.txt...): bỏ dòng PRINTED_PAGE và dòng trống đầu tệp; trang trống thì rỗng."""
    pages = []
    for path in sorted(ocr_dir.glob("p*.txt")):
        body = "\n".join(path.read_text(encoding="utf-8").splitlines()[2:])
        pages.append("" if body.strip() == "[TRANG TRỐNG]" else body)
    return pages


def evaluate_seed(folder: Path, ocr: bool = False) -> tuple[dict, list, dict]:
    """ocr=False: tách cấu trúc trên bản chép chữ đã soát (đo riêng bộ tách).
    ocr=True: chạy toàn luồng như khi tải lên PDF scan mới: source.pdf → OCR → tách cấu trúc (đo OCR + bộ tách)."""
    if ocr:
        pages = extract_pages_text((folder / "source.pdf").read_bytes())
    else:
        pages = seed_pages(folder / "ocr_pages")
    system = json.loads(build_document_from_pages(pages, folder.name + ".pdf"))
    reference = seed_reference(json.loads((folder / "document.json").read_text(encoding="utf-8")))
    return compare(f"{'ocr' if ocr else 'seed'}:{folder.name}", system, reference)


def evaluate(pdf_path: Path, tag: str) -> tuple[dict, list, dict]:
    system = json.loads(auto_build_json_from_pdf(pdf_path.read_bytes(), pdf_path.name))
    return compare(tag, system, parse_reference(pdf_path))


def compare(tag: str, system: dict, reference: list) -> tuple[dict, list, dict]:
    ref_by_key = {unit.key: unit for unit in reference}
    sys_by_key: dict[tuple, dict] = {}
    for unit in system["legal_units"]:
        key = unit_key(unit)
        if key is not None and key not in sys_by_key:
            sys_by_key[key] = unit

    article_ok = {
        key[0]: similar(sys_by_key[key]["title"], ref_by_key[key].title) >= 0.6
        for key in sys_by_key.keys() & ref_by_key.keys() if key[1] is None
    }
    matched = [key for key in ref_by_key if key in sys_by_key and article_ok.get(key[0], False)]
    matched_set = set(matched)
    spurious = [unit for unit in system["legal_units"] if unit_key(unit) not in matched_set]
    missing = [key for key in ref_by_key if key not in matched_set]

    units, reviews = [], {}
    for key in matched:
        uid = f"{tag}:{'.'.join(str(part) for part in key if part is not None)}"
        units.append(SimpleNamespace(unit_id=uid, content=squash(ref_by_key[key].content), source_pages=ref_by_key[key].pages))
        produced = squash(sys_by_key[key]["content"])
        reviews[uid] = review.UnitReview(status="approved", original_content=produced,
                                         edit_count=int(produced != units[-1].content))
    for index, unit in enumerate(spurious):
        uid = f"{tag}:extra{index}"
        units.append(SimpleNamespace(unit_id=uid, content=squash(unit["content"]), source_pages=unit.get("source_pages", [])))
        reviews[uid] = review.UnitReview(status="structure_error")

    truncated = sum(
        len(ref_by_key[key].pages) > 1 and len(squash(sys_by_key[key]["content"])) < 0.9 * len(squash(ref_by_key[key].content))
        for key in matched
    )
    summary = {
        "file": tag,
        "system_document_id": system["document_id"],
        "system_document_no": system["document_no"],
        "reference_units": len(reference),
        "system_units": len(system["legal_units"]),
        "matched": len(matched),
        "spurious": len(spurious),
        "missing": len(missing),
        "recall_percent": round(100 * len(matched) / len(reference), 2) if reference else None,
        "precision_percent": round(100 * len(matched) / len(system["legal_units"]), 2) if system["legal_units"] else None,
        "multi_page_reference_units": sum(len(unit.pages) > 1 for unit in reference),
        "truncated_at_page_end": truncated,
        "missing_examples": ["Điều " + ".".join(str(p) for p in key if p is not None) for key in missing[:6]],
    }
    return summary, units, reviews




METRIC_KEYS = ("approved_units", "reviewed_units", "exact_match_units", "cer_percent", "char_accuracy_percent",
               "wer_percent", "word_accuracy_percent", "unit_accuracy_percent", "structural_fidelity_percent",
               "composite_score")


def report(units: list, reviews: dict) -> dict:
    result = review.build_ocr_report(SimpleNamespace(legal_units=units), reviews)
    return {key: result[key] for key in METRIC_KEYS}


def pool(documents: list[dict], units: list, reviews: dict) -> dict:
    """Gộp nhiều văn bản: tổng đơn vị, chỉ số tính chung, và tách đơn vị nằm trọn 1 trang / nhiều trang."""
    pooled = {key: sum(d[key] for d in documents) for key in ("reference_units", "system_units", "matched", "spurious", "missing")}
    pooled["documents"] = len(documents)
    pooled["recall_percent"] = round(100 * pooled["matched"] / pooled["reference_units"], 2) if pooled["reference_units"] else None
    pooled["precision_percent"] = round(100 * pooled["matched"] / pooled["system_units"], 2) if pooled["system_units"] else None
    pooled["metrics_app"] = report(units, reviews)
    for name, keep in (("single_page", lambda u: len(u.source_pages) <= 1), ("multi_page", lambda u: len(u.source_pages) > 1)):
        chosen = [u for u in units if reviews[u.unit_id].status == "approved" and keep(u)]
        pooled[f"metrics_{name}_units"] = report(chosen, {u.unit_id: reviews[u.unit_id] for u in chosen}) if chosen else None
    return pooled


def show(summary: dict) -> None:
    m = summary["metrics_app"]
    print(f"{summary['file']:58s} recall={summary['recall_percent']}% precision={summary['precision_percent']}% "
          f"CAR={m['char_accuracy_percent']} WAR={m['word_accuracy_percent']} UEM={m['unit_accuracy_percent']} "
          f"SFI={m['structural_fidelity_percent']} Composite={m['composite_score']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", type=Path, nargs="?", help="thư mục bộ dữ liệu PDF (ví dụ 'bo du lieu')")
    parser.add_argument("--seeds", type=Path, help="thư mục văn bản mẫu có ocr_pages/ và document.json (ví dụ data)")
    parser.add_argument("--ocr", action="store_true",
                        help="với --seeds: OCR source.pdf (cần Tesseract 'vie' hoặc GEMINI_API_KEY) thay cho bản chép chữ")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    seed_docs, seed_units, seed_reviews = [], [], {}
    for folder in sorted(args.seeds.iterdir()) if args.seeds else []:
        if (folder / "ocr_pages").is_dir() and (folder / "document.json").is_file() and (folder / "source.pdf").is_file():
            summary, units, reviews = evaluate_seed(folder, ocr=args.ocr)
            summary["metrics_app"] = report(units, reviews)
            seed_docs.append(summary)
            seed_units += units
            seed_reviews.update(reviews)
            show(summary)

    pdfs = sorted(args.dataset.rglob("*.pdf")) if args.dataset else []
    hashes: dict[str, list[str]] = {}
    inventory, documents, all_units, all_reviews = [], [], [], {}
    for pdf_path in pdfs:
        tag = pdf_path.relative_to(args.dataset).as_posix()
        hashes.setdefault(hashlib.sha256(pdf_path.read_bytes()).hexdigest(), []).append(tag)
        with_text, pages = text_page_ratio(pdf_path)
        inventory.append({"file": tag, "pages": pages, "text_pages": with_text})
        if pages == 0 or with_text / pages < 0.8:
            continue
        try:
            summary, units, reviews = evaluate(pdf_path, tag)
        except ImportProblem as exc:
            documents.append({"file": tag, "refused": exc.problems})
            continue
        if summary["reference_units"] == 0:
            summary["note"] = "Không tìm thấy Điều nào: không phải văn bản quy phạm có Điều/Khoản/Điểm"
            documents.append(summary)
            continue
        summary["metrics_app"] = report(units, reviews)
        documents.append(summary)
        all_units += units
        all_reviews.update(reviews)
        show(summary)

    result = {
        "pdfs": len(pdfs),
        "text_layer_pdfs": sum(i["pages"] and i["text_pages"] / i["pages"] >= 0.8 for i in inventory),
        "scan_pdfs": sum(i["text_pages"] == 0 for i in inventory),
        "duplicate_files": [names for names in hashes.values() if len(names) > 1],
        "seed_documents": seed_docs,
        "seed_pooled": pool(seed_docs, seed_units, seed_reviews) if seed_docs else None,
        "documents": documents,
        "pooled": pool([d for d in documents if "metrics_app" in d], all_units, all_reviews),
    }
    print(json.dumps({k: v for k, v in result.items() if k not in ("documents", "seed_documents")}, ensure_ascii=False, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
