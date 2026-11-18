"""Dựng sprite icon cho review_app từ Fluent UI System Icons (MIT, Microsoft).

Tải từng SVG của gói npm @fluentui/svg-icons (phiên bản cố định ở VERSION) qua jsDelivr, đổi màu tô thành
currentColor để icon theo màu chữ, rồi ghép thành app/static/icons.svg (mỗi icon là một <symbol id="tên">).
App chỉ đọc tệp đã dựng, không gọi CDN khi chạy. Thêm icon: thêm tên vào ICONS rồi chạy lại

    python scripts/build_icons.py

Tên icon xem ở https://github.com/microsoft/fluentui-system-icons (cột "20", kiểu regular hoặc filled).
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

VERSION = "1.1.330"
BASE_URL = f"https://cdn.jsdelivr.net/npm/@fluentui/svg-icons@{VERSION}/icons/"
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons.svg"

# id trong sprite -> tên tệp Fluent (không có .svg)
ICONS = {
    "app": "scales_20_regular",
    "home": "home_20_regular",
    "folder": "folder_20_filled",
    "folder-open": "folder_open_20_filled",
    "folder-add": "folder_add_20_regular",
    "document": "document_20_regular",
    "document-pdf": "document_pdf_20_regular",
    "tree": "text_bullet_list_tree_20_regular",
    "back": "arrow_left_20_regular",
    "forward": "arrow_right_20_regular",
    "up": "arrow_up_20_regular",
    "refresh": "arrow_clockwise_20_regular",
    "search": "search_20_regular",
    "chevron-right": "chevron_right_20_regular",
    "chevron-down": "chevron_down_20_regular",
    "chevron-left": "chevron_left_20_regular",
    "more": "more_horizontal_20_regular",
    "menu": "navigation_20_regular",
    "panel-left": "panel_left_20_regular",
    "panel-right": "panel_right_20_regular",
    "person": "person_20_regular",
    "person-add": "person_add_20_regular",
    "people": "people_20_regular",
    "assign": "people_team_20_regular",
    "stats": "data_bar_vertical_20_regular",
    "settings": "settings_20_regular",
    "key": "key_20_regular",
    "sign-out": "sign_out_20_regular",
    "lock": "lock_closed_20_regular",
    "unlock": "lock_open_20_regular",
    "moon": "weather_moon_20_regular",
    "sun": "weather_sunny_20_regular",
    "eye": "eye_20_regular",
    "eye-off": "eye_off_20_regular",
    "add": "add_20_regular",
    "upload": "arrow_upload_20_regular",
    "download": "arrow_download_20_regular",
    "delete": "delete_20_regular",
    "check": "checkmark_20_regular",
    "check-circle": "checkmark_circle_20_regular",
    "edit": "edit_20_regular",
    "flag": "flag_20_regular",
    "warning": "warning_20_regular",
    "error": "error_circle_20_regular",
    "info": "info_20_regular",
    "undo": "arrow_undo_20_regular",
    "reset": "arrow_reset_20_regular",
    "save": "save_20_regular",
    "dismiss": "dismiss_20_regular",
    "history": "history_20_regular",
    "zoom-in": "zoom_in_20_regular",
    "zoom-out": "zoom_out_20_regular",
    "fit-page": "page_fit_20_regular",
    "fit-width": "arrow_autofit_width_20_regular",
    "font-size": "text_font_size_20_regular",
    "inbox": "mail_inbox_20_regular",
    "tasks": "task_list_square_ltr_20_regular",
    "filter": "filter_20_regular",
    "expand-all": "arrow_expand_all_20_regular",
    "collapse-all": "arrow_collapse_all_20_regular",
    "gauge": "gauge_20_regular",
    "compare": "split_vertical_20_regular",
}

SVG_RE = re.compile(r"<svg\b([^>]*)>(.*)</svg>", re.S)
VIEWBOX_RE = re.compile(r'viewBox="([^"]+)"')
FILL_RE = re.compile(r'fill="(?!none)[^"]*"')


def fetch(name: str) -> str:
    with urllib.request.urlopen(BASE_URL + name + ".svg", timeout=30) as response:  # noqa: S310 (URL cố định)
        return response.read().decode("utf-8")


def to_symbol(symbol_id: str, svg: str) -> str:
    match = SVG_RE.search(svg)
    if not match:
        raise ValueError(f"{symbol_id}: không phải SVG")
    viewbox = VIEWBOX_RE.search(match.group(1))
    body = FILL_RE.sub('fill="currentColor"', match.group(2).strip())
    return f'<symbol id="{symbol_id}" viewBox="{viewbox.group(1) if viewbox else "0 0 20 20"}">{body}</symbol>'


def main() -> int:
    symbols, missing = [], []
    for symbol_id, name in ICONS.items():
        try:
            symbols.append(to_symbol(symbol_id, fetch(name)))
        except Exception as exc:  # noqa: BLE001 (gom lỗi để báo một lần)
            missing.append(f"{symbol_id} ({name}): {exc}")
    if missing:
        print("Không tải được:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    header = (f"<!-- Fluent UI System Icons {VERSION} (MIT, Microsoft). Dựng bằng scripts/build_icons.py, không sửa tay. -->\n")
    OUT.write_text(header + '<svg xmlns="http://www.w3.org/2000/svg">\n'
                   + "\n".join(symbols) + "\n</svg>\n", encoding="utf-8", newline="\n")
    print(f"Đã ghi {len(symbols)} icon vào {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
