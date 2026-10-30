"""Tạo tests/fixtures/data/ND254_2026/source.pdf: PDF mẫu 3 trang khớp với document.json cùng thư mục.

Chỉ cần chạy lại khi muốn đổi PDF mẫu (cần reportlab và font DejaVuSans để có dấu tiếng Việt).
    python scripts/make_sample_pdf.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/times.ttf",
]

PAGES = [
    [
        "Chương II",
        "Điều 9. [Tên Điều 9 mẫu]",
        "1. [Nguyên văn Khoản 1, chỗ giữ chỗ]",
        "a) [Nguyên văn Điểm a, chỗ giữ chỗ]",
        "",
        "Trang mẫu 1/3 (dữ liệu minh họa, không phải nguyên văn pháp luật)",
    ],
    [
        "b) [Nguyên văn Điểm b, chỗ giữ chỗ]",
        "2. [Nguyên văn Khoản 2, chỗ giữ chỗ]",
        "",
        "Trang mẫu 2/3 (dữ liệu minh họa, không phải nguyên văn pháp luật)",
    ],
    [
        "Điều 10. [Tên Điều 10 mẫu]",
        "1. [Nguyên văn Khoản 1 của Điều 10, chỗ giữ chỗ]",
        "",
        "Trang mẫu 3/3 (dữ liệu minh họa, không phải nguyên văn pháp luật)",
    ],
]


def main() -> None:
    font_path = next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)
    if font_path is None:
        raise SystemExit("Không tìm thấy font TTF hỗ trợ tiếng Việt; sửa FONT_CANDIDATES.")
    pdfmetrics.registerFont(TTFont("Body", font_path))

    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "data" / "ND254_2026" / "source.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    for lines in PAGES:
        y = height - 90
        for line in lines:
            pdf.setFont("Body", 13)
            pdf.drawString(70, y, line)
            y -= 28
        pdf.showPage()
    pdf.save()
    print(f"Đã tạo {out}")


if __name__ == "__main__":
    main()
