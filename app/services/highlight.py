"""Tìm vùng trên ảnh trang PDF tương ứng với một Điều/Khoản/Điểm để tô sáng ở khung trái.

Hai cách, không cần cài thêm công cụ nào:
- PDF có lớp chữ (PDF số hoặc PDF scan đã OCR sẵn): lấy toạ độ từng ký tự bằng pypdfium2, so khớp nội dung đơn vị
  với chữ trên trang và tô đúng các dòng chứa nó (`method = "text"`).
- PDF chỉ có ảnh scan: tách ảnh trang thành các khối đoạn văn (theo khoảng trắng giữa các dòng và thụt đầu dòng),
  rồi ghép thứ tự các khối với thứ tự đoạn văn của các đơn vị trên trang đó (`method = "layout"`, là vị trí ước lượng).

Toạ độ trả về chuẩn hoá theo kích thước trang: (trái, trên, phải, dưới) trong khoảng 0..1.
"""
from __future__ import annotations

import logging
import math
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .render_pdf import PDFIUM_LOCK

log = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Highlight:
    rects: tuple[Rect, ...]
    method: str | None  # "text" (chính xác), "layout" (ước lượng) hoặc None (không tìm được)


NO_HIGHLIGHT = Highlight((), None)


# ---------------------------------------------------------------- so khớp chữ

def _fold(ch: str) -> str:
    """Bỏ dấu, viết thường; chỉ giữ chữ và số (để chữ trên PDF và chữ trong JSON so được với nhau)."""
    if ch in "đĐ":
        return "d"
    base = unicodedata.normalize("NFKD", ch)
    out = "".join(c for c in base if not unicodedata.combining(c)).lower()
    return "".join(c for c in out if c.isalnum())


def _normalize(text: str) -> tuple[str, list[int]]:
    """Chuỗi đã chuẩn hoá và vị trí ký tự gốc của từng ký tự trong đó."""
    chars: list[str] = []
    origin: list[int] = []
    for index, ch in enumerate(text):
        for folded in _fold(ch):
            chars.append(folded)
            origin.append(index)
    return "".join(chars), origin


def _find_anchor(haystack: str, needle: str, from_end: bool, lo: int = 0) -> int | None:
    """Vị trí đầu (hoặc cuối) của `needle` trong `haystack[lo:]`, lần xuất hiện đầu tiên.

    Thử đoạn neo dài trước rồi ngắn dần; cuối cùng so mờ (lớp chữ OCR có thể sai vài ký tự)."""
    if not needle:
        return None
    for size in (60, 40, 24, 14):
        if len(needle) < size and size != 14:
            continue
        anchor = needle[-size:] if from_end else needle[:size]
        hit = haystack.find(anchor, lo)
        if hit >= 0:
            return hit + len(anchor) if from_end else hit
    from difflib import SequenceMatcher

    anchor = needle[-40:] if from_end else needle[:40]
    match = SequenceMatcher(None, haystack, anchor, autojunk=False).find_longest_match(lo, len(haystack), 0, len(anchor))
    if match.size >= max(10, len(anchor) // 2):
        start = match.a - match.b
        return min(len(haystack), start + len(anchor)) if from_end else max(lo, start)
    return None


def _span_on_page(page_text: str, content: str, heading: str, starts_here: bool, ends_here: bool) -> tuple[int, int] | None:
    """Khoảng [đầu, cuối) của đơn vị trong chữ của trang (đều đã chuẩn hoá).

    `heading` là dòng "Điều N. Tên điều" (chỉ có ở cấp Điều): nó nằm ngay trước nội dung và hầu như không lặp lại,
    nên là mốc bắt đầu chắc chắn nhất."""
    if not page_text or not content:
        return None
    head = page_text.find(heading) if starts_here and heading else -1
    whole = page_text.find(content, max(head, 0))
    if whole >= 0:
        return (head if 0 <= head and whole - head <= len(heading) + 8 else whole), whole + len(content)
    if starts_here:
        start = head if head >= 0 else _find_anchor(page_text, content, from_end=False)
    else:
        start = 0  # phần đầu nằm ở trang trước
    end = _find_anchor(page_text, content, from_end=True, lo=start or 0) if ends_here else len(page_text)
    if start is None and end is None:
        return None
    if start is None:
        start = max(0, end - len(content))
    if end is None:
        end = min(len(page_text), start + len(content)) if ends_here else len(page_text)
    return (start, end) if end > start else None


# ---------------------------------------------------------------- PDF có lớp chữ

@dataclass(frozen=True)
class _TextLayer:
    normalized: str
    origin: tuple[int, ...]  # vị trí ký tự gốc (trong `raw`) của từng ký tự trong `normalized`
    raw: str
    boxes: tuple[Rect | None, ...]  # hộp của từng ký tự gốc, chuẩn hoá theo trang; None với khoảng trắng


@lru_cache(maxsize=64)  # mỗi trang giữ hộp của ~2000 ký tự
def _text_layer(pdf_path: str, stamp: tuple[int, int], page: int) -> _TextLayer | None:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            pdf_page = pdf[page - 1]
            width, height = pdf_page.get_size()
            textpage = pdf_page.get_textpage()
            try:
                count = textpage.count_chars()
                if count < 15:
                    return None
                raw = "".join((textpage.get_text_range(i, 1) or " ")[:1] for i in range(count))
                raw_boxes = [textpage.get_charbox(i) for i in range(count)]
            finally:
                textpage.close()
                pdf_page.close()
        finally:
            pdf.close()
    normalized, origin = _normalize(raw)
    if len(normalized) < 15:
        return None
    boxes: list[Rect | None] = []
    for ch, (left, bottom, right, top) in zip(raw, raw_boxes):
        visible = not ch.isspace() and right > left and top > bottom
        boxes.append((left / width, 1 - top / height, right / width, 1 - bottom / height) if visible else None)
    return _TextLayer(normalized, tuple(origin), raw, tuple(boxes))


def _text_rects(layer: _TextLayer, start: int, end: int) -> tuple[Rect, ...]:
    """Hộp các dòng chữ chứa ký tự [start, end) của chuỗi chuẩn hoá, gộp các dòng liền nhau thành một vùng."""
    first, last = layer.origin[start], layer.origin[end - 1]
    while last + 1 < len(layer.raw) and not layer.raw[last + 1].isspace() and not layer.raw[last + 1].isalnum():
        last += 1  # dấu câu cuối đơn vị (";", ".", ")") bị bỏ khi chuẩn hoá
    lines: list[list[float]] = []
    for box in layer.boxes[first:last + 1]:
        if box is None:
            continue
        left, top, right, bottom = box
        middle = (top + bottom) / 2
        for line in lines:
            if abs(middle - (line[1] + line[3]) / 2) < max(line[3] - line[1], bottom - top) * 0.6:
                line[0], line[1] = min(line[0], left), min(line[1], top)
                line[2], line[3] = max(line[2], right), max(line[3], bottom)
                break
        else:
            lines.append([left, top, right, bottom])
    lines.sort(key=lambda r: r[1])
    regions: list[list[float]] = []
    for line in lines:
        height = line[3] - line[1]
        if regions and line[1] - regions[-1][3] < height * 1.6:  # cách nhau chưa tới ~1,5 dòng: cùng một vùng
            region = regions[-1]
            region[0], region[2], region[3] = min(region[0], line[0]), max(region[2], line[2]), max(region[3], line[3])
        else:
            regions.append(list(line))
    return tuple(_pad((r[0], r[1], r[2], r[3]), 0.006, 0.004) for r in regions)


# ---------------------------------------------------------------- PDF scan: tách khối đoạn văn trên ảnh

@dataclass(frozen=True)
class _Block:
    rect: Rect  # chuẩn hoá theo ảnh
    lines: int
    centered: bool


@dataclass(frozen=True)
class _PageLayout:
    blocks: tuple[_Block, ...]
    line_height: int  # chiều cao một dòng chữ (điểm ảnh), để ước lượng số ký tự trên một dòng
    body_width: int


@lru_cache(maxsize=512)
def _image_layout(image_path: str, stamp: tuple[int, int]) -> _PageLayout:
    with Image.open(image_path) as source:
        gray = source.convert("L")
    width, height = gray.size
    ink = gray.point(lambda v: 255 if v < 150 else 0)
    row_profile = ink.resize((1, height), Image.BOX).tobytes()  # độ đậm mực trung bình của từng hàng điểm ảnh

    bands: list[list[int]] = []  # [đầu, cuối) theo hàng điểm ảnh
    in_band = False
    for y, value in enumerate(row_profile):
        if value > 2 and not in_band:
            bands.append([y, y + 1])
            in_band = True
        elif value > 2:
            bands[-1][1] = y + 1
        else:
            in_band = False
    if not bands:
        return _PageLayout((), 0, 0)
    heights = sorted(b[1] - b[0] for b in bands)
    median = heights[len(heights) // 2]
    heights = [h for h in heights if h <= median * 3] or heights  # bỏ các dải cao bất thường (bảng biểu, con dấu)
    typical = heights[len(heights) * 3 // 4]  # dải dấu thanh rất mỏng nên lấy phân vị 75 thay vì trung vị
    merged: list[list[int]] = []
    for band in bands:  # dấu thanh / dấu mũ tách khỏi dòng chữ: gộp lại
        if merged and band[0] - merged[-1][1] < typical * 0.35 and (band[1] - merged[-1][0]) < typical * 1.6:
            merged[-1][1] = band[1]
        else:
            merged.append(band)
    # trang chỉ có bảng biểu: không có dòng chữ rời nào, giới hạn ở cỡ một dòng chữ thường (~3% chiều cao trang)
    line_height = min(sorted(b[1] - b[0] for b in merged)[len(merged) // 2], max(8, height // 33))

    lines: list[tuple[int, int, int, int]] = []  # trái, trên, phải, dưới (điểm ảnh)
    for top, bottom in merged:
        if bottom - top < max(4, line_height * 0.4):
            continue  # vết bẩn, dấu chấm lẻ
        cols = ink.crop((0, top, width, bottom)).resize((width, 1), Image.BOX).tobytes()
        total = sum(cols)
        if total / 255 * (bottom - top) < line_height * line_height * 0.25:
            continue  # quá ít mực (chưa tới hai chữ): vết bẩn, dấu chấm lẻ
        acc, left, right = 0.0, 0, width
        for x, value in enumerate(cols):
            acc += value
            if acc >= total * 0.003:
                left = x
                break
        acc = 0.0
        for x in range(width - 1, -1, -1):
            acc += cols[x]
            if acc >= total * 0.003:
                right = x + 1
                break
        lines.append((left, top, right, bottom))
    if not lines:
        return _PageLayout((), 0, 0)

    body_left = sorted(line[0] for line in lines)[len(lines) // 5]
    body_right = sorted(line[2] for line in lines)[len(lines) * 4 // 5]
    body_width = max(1, body_right - body_left)
    margin = int(body_width * 0.02)  # vết bẩn ở lề trái/phải không được kéo dài khung tô
    lines = [(max(left, body_left - margin), top, min(right, body_right + margin), bottom) for left, top, right, bottom in lines]
    gaps = [lines[i + 1][1] - lines[i][3] for i in range(len(lines) - 1)]
    line_gap = sorted(gaps)[len(gaps) // 3] if gaps else 0
    pitch = line_height + line_gap

    blocks: list[list[tuple[int, int, int, int]]] = []
    for index, line in enumerate(lines):
        if not blocks:
            blocks.append([line])
            continue
        previous = lines[index - 1]
        gap = line[1] - previous[3]
        indented = line[0] - body_left > body_width * 0.03
        previous_short = previous[2] < body_right - body_width * 0.08
        if gap > line_gap + pitch * 0.45 or (indented and previous_short) or (indented and gap > line_gap + pitch * 0.2):
            blocks.append([line])
        else:
            blocks[-1].append(line)

    result = []
    for block in blocks:
        left = min(line[0] for line in block)
        right = max(line[2] for line in block)
        middle = (left + right) / 2
        centered = abs(middle - (body_left + body_right) / 2) < body_width * 0.08 and (right - left) < body_width * 0.75
        rect = (left / width, block[0][1] / height, right / width, block[-1][3] / height)
        # bảng biểu: kẻ dọc nối các dòng thành một dải cao, nên quy đổi chiều cao ra số dòng
        line_count = sum(max(1, round((bottom - top) / pitch)) for _, top, _, bottom in block)
        result.append(_Block(rect, line_count, centered))
    return _PageLayout(tuple(result), line_height, body_width)


# ---------------------------------------------------------------- PDF scan: thứ tự đoạn văn của các đơn vị

@dataclass(frozen=True)
class _Paragraph:
    owner: str | None  # unit_id; None = tiêu đề Chương/Mục (có trên trang nhưng không thuộc đơn vị nào)
    chars: int
    pages: tuple[int, ...]


def _paragraphs(units) -> list[_Paragraph]:
    """Các đoạn văn theo thứ tự đọc: tiêu đề Điều, phần mở đầu của Điều/Khoản, rồi nội dung đơn vị con."""
    children: dict[str, list] = {}
    for unit in units:
        if unit.parent_unit_id:
            children.setdefault(unit.parent_unit_id, []).append(unit)
    paragraphs = []
    chapter = section = None
    for unit in units:
        if not unit.source_pages:
            continue
        first_page = unit.source_pages[0]
        if unit.article is not None and unit.clause is None and unit.point is None:
            # "Chương II" + tên chương (hoặc "Mục 1" + tên mục) đứng ngay trước Điều đầu tiên của chương / mục
            if (unit.chapter and unit.chapter != chapter) or (unit.section and unit.section != section):
                paragraphs.append(_Paragraph(None, 0, (first_page,)))
            chapter, section = unit.chapter or chapter, unit.section
            paragraphs.append(_Paragraph(unit.unit_id, len(unit.title or "") + 10, (first_page,)))
        lines = [line for line in unit.content.split("\n") if line.strip()]
        kids = children.get(unit.unit_id, [])
        if kids:
            kid_lines = sum(len([x for x in kid.content.split("\n") if x.strip()]) for kid in kids)
            own = lines[: max(0, len(lines) - kid_lines)]
            pages = (first_page,)
        else:
            own = lines
            pages = tuple(unit.source_pages)
        for line in own:
            paragraphs.append(_Paragraph(unit.unit_id, len(line), pages))
    return paragraphs


def _align(paragraphs: list[_Paragraph], blocks: tuple[_Block, ...], page: int, chars_per_line: float) -> tuple[float, dict[int, int]]:
    """Ghép đoạn văn (theo thứ tự) với khối trên ảnh (theo thứ tự) bằng quy hoạch động.

    Trả (tổng chi phí, {chỉ số khối: chỉ số đoạn}). Khối không ghép được là phần không thuộc đơn vị nào
    (số trang, quốc hiệu, căn cứ ban hành, tiêu đề Chương/Mục, chữ ký...)."""
    n, m = len(paragraphs), len(blocks)
    expected = [max(1, math.ceil((p.chars + 6) / chars_per_line)) for p in paragraphs]
    continues_before = [min(p.pages) < page for p in paragraphs]
    continues_after = [max(p.pages) > page for p in paragraphs]
    free_lead = not continues_before[0]  # trang mở đầu bằng phần không thuộc đơn vị nào (phần mở đầu văn bản...)
    free_tail = not continues_after[-1]  # trang kết thúc bằng phần không thuộc đơn vị nào (chữ ký, nơi nhận...)

    def match_cost(i: int, lines: int) -> float:
        if paragraphs[i].owner is None:
            return 0.05 if lines <= 6 else 3.0  # tiêu đề Chương/Mục: một hoặc vài dòng
        e = expected[i]
        if (continues_before[i] or continues_after[i]) and lines <= e:
            return 0.15  # đoạn vắt sang trang khác: trên trang này chỉ có một phần
        return abs(e - lines) / max(e, lines) * 2.0

    def skip_block_cost(i: int, j: int) -> float:
        block = blocks[j]
        if block.centered:
            return 0.3 + 0.05 * block.lines  # số trang, tiêu đề Chương/Mục, tên văn bản...
        if block.lines <= 2 and j in (0, m - 1):
            return 0.35  # đầu trang / chân trang
        if (i == 0 and free_lead) or (i == n and free_tail):
            return 0.2 + 0.02 * block.lines
        return 0.9 + 0.25 * block.lines

    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == inf:
                continue
            if i < n:  # đoạn không có trên ảnh: chỉ hợp lý khi nó nằm hẳn ở trang trước / trang sau
                at_edge = (j == 0 and continues_before[i]) or (j == m and continues_after[i])
                step = here + (0.05 if at_edge or paragraphs[i].owner is None else 1.6)
                if step < cost[i + 1][j]:
                    cost[i + 1][j], back[i + 1][j] = step, (i, j, "skip_p")
            if j < m:
                step = here + skip_block_cost(i, j)
                if step < cost[i][j + 1]:
                    cost[i][j + 1], back[i][j + 1] = step, (i, j, "skip_b")
            if i < n and j < m:
                step = here + match_cost(i, blocks[j].lines)
                if step < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = step, (i, j, "match")
            if i < n and j + 1 < m:  # một đoạn bị tách thành hai khối
                step = here + match_cost(i, blocks[j].lines + blocks[j + 1].lines) + (0.1 if paragraphs[i].owner is None else 0.6)
                if step < cost[i + 1][j + 2]:
                    cost[i + 1][j + 2], back[i + 1][j + 2] = step, (i, j, "split")
            if i + 1 < n and j < m and paragraphs[i].owner is not None and paragraphs[i].owner == paragraphs[i + 1].owner:
                # hai đoạn dính thành một khối
                lines = blocks[j].lines
                e = expected[i] + expected[i + 1]
                step = here + abs(e - lines) / max(e, lines) * 2.0 + 0.6
                if step < cost[i + 2][j + 1]:
                    cost[i + 2][j + 1], back[i + 2][j + 1] = step, (i, j, "join")

    pairs: dict[int, int] = {}
    i, j = n, m
    while (i, j) != (0, 0):
        step = back[i][j]
        if step is None:
            break
        pi, pj, kind = step
        if kind == "match":
            pairs[pj] = pi
        elif kind == "split":
            pairs[pj] = pairs[pj + 1] = pi
        elif kind == "join":
            pairs[pj] = pi
        i, j = pi, pj
    return cost[n][m], pairs


def _best_alignment(paragraphs: list[_Paragraph], layout: _PageLayout, page: int) -> dict[int, int]:
    """Số ký tự trên một dòng tỉ lệ với (bề rộng vùng chữ / chiều cao dòng), hệ số đo trên văn bản mẫu ≈ 2,2–2,6.
    Thử vài giá trị quanh mức ước lượng đó, lấy cách ghép rẻ nhất (phạt nhẹ khi lệch xa ước lượng)."""
    estimate = 2.4 * layout.body_width / max(1, layout.line_height)
    best: tuple[float, dict[int, int]] | None = None
    for factor in (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15):
        total, pairs = _align(paragraphs, layout.blocks, page, estimate * factor)
        total += 2.0 * abs(factor - 1.0)
        if best is None or total < best[0] - 1e-9:
            best = (total, pairs)
    return best[1] if best else {}


def _layout_rects(units, unit, page: int, image: Path) -> tuple[Rect, ...]:
    stat = image.stat()
    layout = _image_layout(str(image), (stat.st_size, stat.st_mtime_ns))
    blocks = layout.blocks
    if not blocks:
        return ()
    on_page = [p for p in _paragraphs(units) if page in p.pages]
    if not on_page:
        return ()
    targets = _subtree(units, unit.unit_id)
    whole_page = [j for j, block in enumerate(blocks) if not (block.lines == 1 and block.centered and j in (0, len(blocks) - 1))]
    alone = all(p.owner in targets for p in on_page if p.owner is not None)  # trên trang chỉ có đơn vị này
    if alone and unit.source_pages[0] < page < unit.source_pages[-1]:  # trang giữa của phụ lục, Điều dài nhiều trang
        chosen = whole_page
    else:
        pairs = _best_alignment(on_page, layout, page)
        chosen = [j for j, i in pairs.items() if on_page[i].owner in targets] or (whole_page if alone else [])
    if not chosen:
        return ()
    # Một đơn vị cùng các đơn vị con của nó là một đoạn liền mạch: mọi khối nằm giữa (bảng, đoạn bị tách...) cũng thuộc nó.
    boxes = [blocks[j].rect for j in range(min(chosen), max(chosen) + 1)]
    rect = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return (_pad(rect, 0.008, 0.004),)


def _subtree(units, unit_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for unit in units:
        if unit.parent_unit_id:
            children.setdefault(unit.parent_unit_id, []).append(unit.unit_id)
    found, stack = set(), [unit_id]
    while stack:
        current = stack.pop()
        if current not in found:
            found.add(current)
            stack.extend(children.get(current, []))
    return found


def _pad(rect: Rect, dx: float, dy: float) -> Rect:
    left, top, right, bottom = rect
    return (max(0.0, left - dx), max(0.0, top - dy), min(1.0, right + dx), min(1.0, bottom + dy))


# ---------------------------------------------------------------- điểm vào

def locate(loaded, unit_id: str | None, page: int) -> Highlight:
    """Vùng cần tô sáng của đơn vị `unit_id` trên trang `page` của văn bản đã nạp (LoadedDocument)."""
    unit = loaded.units_by_id.get(unit_id) if unit_id else None
    if unit is None or page not in unit.source_pages:
        return NO_HIGHLIGHT
    try:
        pdf_path = loaded.pdf_path
        stat = pdf_path.stat()
        layer = _text_layer(str(pdf_path), (stat.st_size, stat.st_mtime_ns), page)
        if layer is not None:
            content, _ = _normalize(unit.content)
            heading = ""
            if unit.article is not None and unit.clause is None and unit.point is None:
                heading, _ = _normalize(f"Điều {unit.article}. {unit.title or ''}")
            span = _span_on_page(layer.normalized, content, heading, page == unit.source_pages[0], page == unit.source_pages[-1])
            rects = _text_rects(layer, *span) if span else ()
            if rects:
                return Highlight(rects, "text")
            # lớp chữ không khớp nội dung (OCR kém, nội dung đã sửa nhiều...): dùng cách ước lượng theo bố cục
        rects = _layout_rects(loaded.doc.legal_units, unit, page, loaded.page_image(page))
        return Highlight(rects, "layout") if rects else NO_HIGHLIGHT
    except Exception:  # tô sáng chỉ là phần hỗ trợ: lỗi ở đây không được làm hỏng khung xem PDF
        log.warning("Không xác định được vùng tô sáng của %s ở trang %s", unit_id, page, exc_info=True)
        return NO_HIGHLIGHT


__all__ = ["NO_HIGHLIGHT", "Highlight", "locate"]
