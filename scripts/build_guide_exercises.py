"""Cắt ảnh vùng PDF cho phần Bài tập thực hành của trang hướng dẫn (app/static/guide/).

Mỗi bài lấy một đơn vị của văn bản mẫu trong data/: dựng ảnh trang (services/render_pdf), tìm vùng của đơn vị
trên trang như khung tô vàng của màn soát (services/highlight.locate), cắt vùng đó kèm lề rồi ghi WebP vào
app/static/guide/img/ex-<số>.webp. Bài "blur" làm mờ ảnh cắt để giả bản scan không đọc rõ.
Chữ OCR đã cài lỗi và đáp án viết tay trong app/static/guide/data.js; danh sách đơn vị ở EXERCISES phải khớp.

    python scripts/build_guide_exercises.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.highlight import locate  # noqa: E402
from app.services.store import DocumentStore  # noqa: E402

OUT = ROOT / "app" / "static" / "guide" / "img"
MAX_WIDTH = 960
PAD_X, PAD_Y = 0.015, 0.012  # lề quanh vùng đơn vị, theo tỉ lệ khổ trang

# (số bài, văn bản, đơn vị, hiệu ứng). Giữ đúng thứ tự với EXERCISES trong app/static/guide/data.js.
EXERCISES = [
    (1, "ND254_2026", "ND254_2026_D5_K1_a", None),
    (2, "ND254_2026", "ND254_2026_D9_K4_h", None),
    (3, "ND254_2026", "ND254_2026_D10_K1_g", None),
    (4, "ND254_2026", "ND254_2026_D5_K3_a", "with-next"),
    (5, "ND254_2026", "ND254_2026_D6_K2_d", "blur"),
    (6, "ND254_2026", "ND254_2026_D10_K1_e", None),
    (7, "ND254_2026", "ND254_2026_D1_K2_b", None),
]


def unit_box(loaded, unit_id: str) -> tuple[int, tuple[float, float, float, float]]:
    unit = loaded.units_by_id[unit_id]
    page = unit.source_pages[0]
    rects = locate(loaded, unit_id, page).rects
    if not rects:
        raise SystemExit(f"Không tìm được vùng của {unit_id} trên trang {page}")
    return page, (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def next_unit_id(loaded, unit_id: str) -> str:
    ids = [u.unit_id for u in loaded.doc.legal_units]
    return ids[ids.index(unit_id) + 1]


def main() -> int:
    store = DocumentStore(ROOT / "data")
    OUT.mkdir(parents=True, exist_ok=True)
    for number, document_id, unit_id, effect in EXERCISES:
        loaded = store.get(document_id)
        page, (left, top, right, bottom) = unit_box(loaded, unit_id)
        if effect == "with-next":  # bài lỗi cấu trúc: ảnh có cả điểm kế tiếp, để thấy hai điểm bị gộp
            next_page, box = unit_box(loaded, next_unit_id(loaded, unit_id))
            if next_page == page:
                left, top, right, bottom = min(left, box[0]), min(top, box[1]), max(right, box[2]), max(bottom, box[3])
        image = Image.open(loaded.page_image(page)).convert("RGB")
        w, h = image.size
        crop = image.crop((
            max(0, int((left - PAD_X) * w)), max(0, int((top - PAD_Y) * h)),
            min(w, int((right + PAD_X) * w)), min(h, int((bottom + PAD_Y) * h)),
        ))
        if crop.width > MAX_WIDTH:
            crop = crop.resize((MAX_WIDTH, round(crop.height * MAX_WIDTH / crop.width)), Image.LANCZOS)
        if effect == "blur":
            crop = crop.filter(ImageFilter.GaussianBlur(radius=2.6))
        target = OUT / f"ex-{number}.webp"
        crop.save(target, "WEBP", quality=82)
        print(f"{target.name}: {unit_id} trang {page}, {crop.width}x{crop.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
