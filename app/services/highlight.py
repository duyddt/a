"""Tìm vùng trên ảnh trang PDF tương ứng với một Điều/Khoản/Điểm để tô sáng ở khung trái.

Ba cách, theo thứ tự ưu tiên:
- PDF có lớp chữ (PDF số hoặc PDF scan đã OCR sẵn): lấy toạ độ từng ký tự bằng pypdfium2, so khớp nội dung đơn vị
  với chữ trên trang và tô đúng các dòng chứa nó (`method = "text"`).
- PDF chỉ có ảnh (bản scan, ảnh chụp): dùng toạ độ từng từ do OCR trả về (page_words.py, lưu trong PostgreSQL) và so
  khớp y như trên (`method = "ocr"`). Chữ OCR lấy trên đúng ảnh đã OCR khi nhập văn bản nên gần như trùng nội dung.
- Trang chưa có toạ độ từ và máy không có Tesseract: tách ảnh trang thành các khối đoạn văn (khoảng trắng giữa các
  dòng, thụt đầu dòng) rồi ghép thứ tự khối với thứ tự đoạn văn của các đơn vị (`method = "layout"`, chỉ là ước lượng).

So khớp chữ đi TUẦN TỰ theo thứ tự đơn vị trên trang: đơn vị sau chỉ được bắt đầu sau đơn vị trước, nên các đoạn ngắn
lặp lại ("a) Thuế;" ở hai Khoản) không bị nhận nhầm; đơn vị kết thúc trước đơn vị kế tiếp không thuộc nó; số trang ở
đầu / cuối trang không bao giờ bị tô. Chữ OCR của ảnh chụp tay hay lẫn nhiễu ở mép giấy và mất vài ký tự đầu dòng: đơn vị
nối trang bỏ qua vài dòng mép không có trong nội dung, và chỗ bắt đầu tìm mờ không lấn sang cuối dòng trên.

Toạ độ trả về chuẩn hoá theo kích thước trang: (trái, trên, phải, dưới) trong khoảng 0..1.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .render_pdf import PDFIUM_LOCK

log = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Highlight:
    rects: tuple[Rect, ...]
    method: str | None  # "text" / "ocr" (theo chữ, chính xác), "layout" (ước lượng) hoặc None (không tìm được)


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


def _find_anchor(haystack: str, needle: str, from_end: bool, lo: int = 0, hi: int | None = None) -> int | None:
    """Vị trí đầu (hoặc cuối) của `needle` trong `haystack[lo:hi]`, lần xuất hiện đầu tiên.

    Thử đoạn neo dài trước rồi ngắn dần; cuối cùng so mờ (chữ OCR sai vài ký tự, nội dung đã được sửa)."""
    hi = len(haystack) if hi is None else hi
    if not needle or hi <= lo:
        return None
    for size in (60, 40, 24, 14):
        if len(needle) < size and size != 14:
            continue
        anchor = needle[-size:] if from_end else needle[:size]
        hit = haystack.find(anchor, lo, hi)
        if hit >= 0:
            return hit + len(anchor) if from_end else hit
    return _fuzzy_edge(haystack, needle[-40:] if from_end else needle[:40], from_end, lo, hi)


def _rfind_end(haystack: str, needle: str, lo: int, hi: int) -> int | None:
    """Vị trí ngay sau lần xuất hiện CUỐI CÙNG của phần đuôi `needle` trong `haystack[lo:hi]`.

    Dùng cho chỗ kết thúc của đơn vị: phần đuôi (vài chục ký tự) có thể lặp lại ngay trong đơn vị, nhưng sau chỗ kết
    thúc thật thì không còn (đã sang đơn vị khác, chữ ký, nơi nhận)."""
    if not needle or hi <= lo:
        return None
    for size in (60, 40, 24, 14, 8):
        if len(needle) < size and size not in (14, 8):
            continue
        anchor = needle[-size:]
        hit = haystack.rfind(anchor, lo, hi)
        if hit >= 0:
            return hit + len(anchor)
    return _fuzzy_edge(haystack, needle[-40:], True, lo, hi)


def _fuzzy_edge(haystack: str, anchor: str, from_end: bool, lo: int, hi: int) -> int | None:
    """So mờ `anchor` (đầu hoặc đuôi nội dung) với `haystack[lo:hi]`: lấy đoạn trùng dài nhất, rồi chỉnh mép theo khối
    trùng đầu tiên (hoặc cuối cùng) quanh đó, để chữ bị thêm / bớt ở đầu nội dung không làm lệch chỗ bắt đầu."""
    from difflib import SequenceMatcher

    match = SequenceMatcher(None, haystack, anchor, autojunk=False).find_longest_match(lo, hi, 0, len(anchor))
    if match.size < max(8, len(anchor) // 2):
        return None
    if not from_end:
        head = anchor[:match.b]
        if not head:
            return match.a
        window_start = max(lo, match.a - len(head) - 10)
        for block in SequenceMatcher(None, haystack[window_start:match.a], head, autojunk=False).get_matching_blocks():
            if block.size >= 3:
                return max(lo, window_start + block.a - block.b)
        return max(lo, match.a - match.b)
    tail = anchor[match.b + match.size:]
    end = match.a + match.size
    if not tail:
        return end
    window = haystack[end:min(hi, end + len(tail) + 10)]
    blocks = [b for b in SequenceMatcher(None, window, tail, autojunk=False).get_matching_blocks() if b.size >= 3]
    if blocks:
        last = blocks[-1]
        return min(hi, end + last.a + last.size + (len(tail) - last.b - last.size))
    return min(hi, end + len(tail))


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


def _words_layer(words) -> _TextLayer:
    """Tầng chữ dựng từ các từ OCR (page_words.Word): mỗi ký tự lấy một phần hộp của từ chứa nó (chia đều theo bề
    ngang), các từ cùng dòng cách nhau một dấu cách, các dòng cách nhau một dấu xuống dòng."""
    raw: list[str] = []
    boxes: list[Rect | None] = []
    previous_line = None
    for word in words:
        if raw:
            raw.append("\n" if word.line != previous_line else " ")
            boxes.append(None)
        previous_line = word.line
        left, top, right, bottom = word.box
        step = (right - left) / max(1, len(word.text))
        for k, ch in enumerate(word.text):
            raw.append(ch)
            boxes.append((left + step * k, top, left + step * (k + 1), bottom))
    text = "".join(raw)
    normalized, origin = _normalize(text)
    return _TextLayer(normalized, tuple(origin), text, tuple(boxes))


_PAGE_NUMBER = re.compile(r"[-–—.\s]*\d{1,4}[-–—.\s]*")


def _body_bounds(layer: _TextLayer) -> tuple[int, int]:
    """[đầu, cuối) phần thân trang trong chuỗi chuẩn hoá: bỏ dòng chỉ có số trang ở đầu và ở cuối trang."""
    lines, offset = [], 0
    for line in layer.raw.split("\n"):
        if line.strip():
            lines.append((offset, offset + len(line), line.strip()))
        offset += len(line) + 1
    lo, hi = 0, len(layer.normalized)
    if lines and _PAGE_NUMBER.fullmatch(lines[0][2]):
        lo = bisect_left(layer.origin, lines[0][1])
    if len(lines) > 1 and _PAGE_NUMBER.fullmatch(lines[-1][2]):
        hi = bisect_left(layer.origin, lines[-1][0])
    return (lo, hi) if hi > lo else (0, len(layer.normalized))


def _line_starts(layer: _TextLayer) -> tuple[int, ...]:
    """Chỉ số (trong chuỗi chuẩn hoá) nơi mỗi dòng chữ bắt đầu."""
    starts, offset = set(), 0
    for line in layer.raw.split("\n"):
        starts.add(bisect_left(layer.origin, offset))
        offset += len(line) + 1
    return tuple(sorted(starts))


MAX_NOISE_LINES = 3  # số dòng nhiễu tối đa bỏ qua ở mép trang


def _line_in_content(line: str, matcher) -> bool:
    """Dòng (đã chuẩn hoá) có trong nội dung không: dòng ngắn phải có nguyên văn, dòng dài chỉ cần nửa dòng trùng."""
    content = matcher.b
    if len(line) < 4:
        return line in content
    matcher.set_seq1(line)
    return matcher.find_longest_match(0, len(line), 0, len(content)).size >= max(4, len(line) // 2)


def _content_edge(text: str, content: str, lo: int, hi: int, breaks: tuple[int, ...], from_end: bool) -> int:
    """Mép đầu (hoặc cuối) phần của đơn vị nằm trong `text[lo:hi]` khi đơn vị nối từ trang trước (hoặc còn tiếp ở trang
    sau): bỏ tối đa MAX_NOISE_LINES dòng ở mép không có trong nội dung (nhiễu OCR ở mép ảnh, số trang lẫn ký tự lạ).
    Không dòng nào khớp thì giữ nguyên mép."""
    from difflib import SequenceMatcher

    cuts = [lo, *(b for b in breaks if lo < b < hi), hi]
    spans = [(a, b) for a, b in pairwise(cuts) if b > a]
    if from_end:
        spans.reverse()
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(content)
    for a, b in spans[:MAX_NOISE_LINES + 1]:
        if _line_in_content(text[a:b], matcher):
            return b if from_end else a
    return hi if from_end else lo


def _snap_start(text: str, hit: int, heads: tuple[str, ...], breaks: tuple[int, ...], hi: int) -> int:
    """Chỗ bắt đầu tìm mờ rơi vào vài ký tự cuối dòng trên mà các ký tự đó không phải đầu đơn vị (OCR mất nhãn "c) Tr" ở
    đầu dòng nên phần còn thiếu bị bù bằng chữ cuối dòng trên): dời xuống đầu dòng sau."""
    k = bisect_right(breaks, hit)
    if k < len(breaks) and breaks[k] < hi:
        gap = breaks[k] - hit
        if gap <= 6 and not any(head[:gap] == text[hit:breaks[k]] for head in heads if head):
            return breaks[k]
    return hit


def _heading(unit) -> str:
    """Dòng "Điều N. Tên điều" đã chuẩn hoá (chỉ cấp Điều mới có, và nó không nằm trong `content`)."""
    if unit.article is not None and unit.clause is None and unit.point is None:
        return _normalize(f"Điều {unit.article}. {unit.title or ''}")[0]
    return ""


def _unit_starts(text: str, on_page: list, page: int, lo: int, hi: int,
                 breaks: tuple[int, ...] = ()) -> dict[str, int | None]:
    """Chỗ bắt đầu của từng đơn vị trên trang, tìm lần lượt theo thứ tự đơn vị: đơn vị con bắt đầu từ chỗ của cha trở
    đi, đơn vị kế tiếp (không phải con) bắt đầu sau đó. Đơn vị nối từ trang trước bắt đầu ở đầu phần thân trang (bỏ vài
    dòng nhiễu ở mép). `breaks`: chỗ bắt đầu các dòng (_line_starts)."""
    starts: dict[str, int | None] = {}
    pos, last_id = lo, None
    parent_of = {u.unit_id: u.parent_unit_id for u in on_page}
    for unit in on_page:
        if unit.source_pages[0] < page:
            starts[unit.unit_id] = _content_edge(text, _normalize(unit.content)[0], lo, hi, breaks, from_end=False)
            continue
        ancestor, inside = parent_of.get(unit.unit_id), False
        while ancestor is not None:
            if ancestor == last_id:
                inside = True
                break
            ancestor = parent_of.get(ancestor)
        frm = pos if inside or last_id is None else min(hi, pos + 1)
        content = _normalize(unit.content)[0]
        hit = _find_anchor(text, content, from_end=False, lo=frm, hi=hi)
        heading = _heading(unit)
        if heading:
            exact = text.find(heading, frm, hi)
            if exact >= 0 and (hit is None or exact <= hit):
                hit = exact
            elif hit is not None:  # tên Điều bị OCR sai: tìm mờ ngay trước nội dung
                found = _fuzzy_edge(text, heading, False, max(frm, hit - len(heading) - 12), hit)
                hit = found if found is not None else hit
        if hit is not None:
            hit = _snap_start(text, hit, (heading, content), breaks, hi)
        starts[unit.unit_id] = hit
        if hit is not None:
            pos, last_id = hit, unit.unit_id
    return starts


def _locate_in_layer(layer: _TextLayer, units, unit, page: int) -> tuple[Rect, ...] | None:
    """Vùng tô của `unit` trên trang `page` theo tầng chữ (lớp chữ của PDF hoặc chữ OCR kèm toạ độ).

    None: không tìm được chỗ bắt đầu. (): tìm được nhưng trên trang này đơn vị không có dòng nào (đơn vị nối trang mà
    đơn vị kế tiếp đã bắt đầu ngay đầu trang, ví dụ phần "nối" chỉ là số trang bị tách nhầm vào nội dung)."""
    text = layer.normalized
    lo, hi = _body_bounds(layer)
    on_page = [u for u in units if page in u.source_pages]
    breaks = _line_starts(layer)
    starts = _unit_starts(text, on_page, page, lo, hi, breaks)
    start = starts.get(unit.unit_id)
    if start is None:
        return None
    own = _subtree(units, unit.unit_id)
    index = next(i for i, u in enumerate(on_page) if u.unit_id == unit.unit_id)
    continues = unit.source_pages[0] < page  # mọi đơn vị sau nó trên trang đều bắt đầu sau phần nối này
    later = [starts[u.unit_id] for u in on_page[index + 1:]
             if u.unit_id not in own and starts[u.unit_id] is not None and (continues or starts[u.unit_id] > start)]
    limit = min(later) if later else hi
    if limit <= start:
        return ()
    if page < unit.source_pages[-1]:  # còn tiếp ở trang sau: tới hết phần thân trang (hoặc tới đơn vị kế tiếp)
        end = _content_edge(text, _normalize(unit.content)[0], start, limit, breaks, from_end=True)
    else:
        end = _rfind_end(text, _normalize(unit.content)[0], start, limit)
        if end is None:
            end = limit if later else min(hi, start + len(_normalize(unit.content)[0]))
    return _text_rects(layer, start, end) if end > start else ()


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


def _rects_from_layer(layer: _TextLayer, units, unit, page: int) -> tuple[Rect, ...] | None:
    """Vùng tô theo tầng chữ; () nếu đơn vị không có dòng nào trên trang, None nếu không tìm thấy đơn vị."""
    rects = _locate_in_layer(layer, units, unit, page)
    if rects is not None:
        return rects
    # không tìm được chỗ bắt đầu theo thứ tự: tìm riêng đơn vị này như trước (neo đầu / đuôi độc lập)
    span = _span_on_page(layer.normalized, _normalize(unit.content)[0], _heading(unit),
                         page == unit.source_pages[0], page == unit.source_pages[-1])
    return (_text_rects(layer, *span) or None) if span else None


def _page_words(loaded, page: int):
    """Toạ độ từ OCR của trang (đã lưu, hoặc OCR ngay nếu máy có Tesseract); lỗi thì coi như chưa có."""
    from . import page_words

    try:
        return page_words.for_page(loaded, page)
    except Exception:
        log.warning("Không lấy được toạ độ chữ OCR trang %s", page, exc_info=True)
        return None


def locate(loaded, unit_id: str | None, page: int) -> Highlight:
    """Vùng cần tô sáng của đơn vị `unit_id` trên trang `page` của văn bản đã nạp (LoadedDocument)."""
    unit = loaded.units_by_id.get(unit_id) if unit_id else None
    if unit is None or page not in unit.source_pages:
        return NO_HIGHLIGHT
    try:
        pdf_path = loaded.pdf_path
        stat = pdf_path.stat()
        units = loaded.doc.legal_units
        layer = _text_layer(str(pdf_path), (stat.st_size, stat.st_mtime_ns), page)
        if layer is not None:
            rects = _rects_from_layer(layer, units, unit, page)
            if rects is not None:  # () = đơn vị không có dòng nào trên trang này: không tô, không đoán
                return Highlight(rects, "text") if rects else NO_HIGHLIGHT
            # lớp chữ không khớp nội dung (OCR kém, nội dung đã sửa nhiều...): thử chữ OCR rồi tới ước lượng bố cục
        words = _page_words(loaded, page)
        if words:
            rects = _rects_from_layer(_words_layer(words), units, unit, page)
            if rects is not None:
                return Highlight(rects, "ocr") if rects else NO_HIGHLIGHT
        rects = _layout_rects(units, unit, page, loaded.page_image(page))
        return Highlight(rects, "layout") if rects else NO_HIGHLIGHT
    except Exception:  # tô sáng chỉ là phần hỗ trợ: lỗi ở đây không được làm hỏng khung xem PDF
        log.warning("Không xác định được vùng tô sáng của %s ở trang %s", unit_id, page, exc_info=True)
        return NO_HIGHLIGHT


__all__ = ["NO_HIGHLIGHT", "Highlight", "locate"]
