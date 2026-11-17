"""Danh mục văn bản theo thư mục của bộ dữ liệu ("I. Quản lý thuế" …) và thứ tự trong tệp danh mục Excel."""
import zipfile

import pytest

from app.services.categories import Category, category_from_folder, position_in_folder, read_catalog_order

# ---------------------------------------------------------------- tên thư mục -> danh mục


@pytest.mark.parametrize(("folder", "code", "name", "position"), [
    ("I. Quản lý thuế", "I", "Quản lý thuế", 1),
    ("II. Hóa đơn điện tử", "II", "Hóa đơn điện tử", 2),
    ("IV. Thuế thu nhập cá nhân", "IV", "Thuế thu nhập cá nhân", 4),
    ("IX. Bảo hiểm xã hội", "IX", "Bảo hiểm xã hội", 9),
    ("X. Lao động - Tiền lương", "X", "Lao động - Tiền lương", 10),
    ("XIV.Thuế khác", "XIV", "Thuế khác", 14),
])
def test_numbered_folder_names_become_categories_in_roman_order(folder, code, name, position):
    category = category_from_folder(folder)
    assert (category.code, category.name, category.position) == (code, name, position)
    assert category.label == f"{code}. {name}"


def test_folders_sort_by_roman_number_not_alphabetically():
    folders = ["IV. Thuế TNCN", "IX. BHXH", "I. Quản lý thuế", "X. Lao động", "V. Thuế TNDN", "II. Hóa đơn"]
    ordered = sorted((category_from_folder(name) for name in folders), key=Category.sort_key)
    assert [category.code for category in ordered] == ["I", "II", "IV", "V", "IX", "X"]


def test_folder_without_a_roman_number_keeps_its_name_and_comes_last():
    other = category_from_folder("Văn bản khác")
    assert other.name == "Văn bản khác" and other.label == "Văn bản khác"
    assert other.code == "Van_ban_khac"  # dùng được trên URL
    assert Category.sort_key(other) > Category.sort_key(category_from_folder("X. Lao động"))


def test_a_word_that_only_looks_roman_is_not_a_number():
    category = category_from_folder("Dịch vụ công")  # "D" không có dấu chấm sau nên không phải số La Mã
    assert category.name == "Dịch vụ công" and not category.numbered


# ---------------------------------------------------------------- thứ tự trong tệp danh mục Excel


def write_xlsx(path, rows):
    """Tệp .xlsx tối giản (một sheet, chuỗi dùng chung) như Excel lưu ra."""
    strings, sheet_rows = [], []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column, value in zip("ABCDEF", row, strict=False):
            ref = f"{column}{row_number}"
            if isinstance(value, int):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = "".join(f"<si><t>{text}</t></si>" for text in strings)
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/sharedStrings.xml", f"<sst {ns}>{shared}</sst>")
        book.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>")


HEADER = ["STT", "Nhóm", "Tên văn bản / trích yếu", "Số hiệu", "Tên file theo quy ước", "Link"]


def test_catalog_order_is_read_from_the_file_name_column(tmp_path):
    path = tmp_path / "Danh_muc.xlsx"
    write_xlsx(path, [
        HEADER,
        [1, "I. Quản lý thuế", "Luật Quản lý thuế", "108/2025/QH15", "L108_2025_QH15.pdf", "x"],
        [2, "I. Quản lý thuế", "Nghị định", "252/2026/NĐ-CP", "ND252_2026.pdf", "x"],
        [45, "VIII. Chế độ kế toán", "Thông tư", "133/2016/TT-BTC", "TT133_2016_BTC.pdf (không tải được pdf)", "x"],
    ])
    assert read_catalog_order(path) == {"L108_2025_QH15": 1, "ND252_2026": 2, "TT133_2016_BTC": 45}


def test_a_spreadsheet_that_is_not_a_catalog_gives_no_order(tmp_path):
    path = tmp_path / "khac.xlsx"
    write_xlsx(path, [["A", "B"], ["x", "y"]])
    assert read_catalog_order(path) == {}
    broken = tmp_path / "hong.xlsx"
    broken.write_bytes(b"not a zip")
    assert read_catalog_order(broken) == {}


def summary(document_id, categories=None, total=10, approved=0, flagged=0):
    from app.services.models import DocumentSummary

    counts = {"total": total, "approved": approved, "flagged": flagged, "structure_error": 0,
              "unreviewed": total - approved - flagged, "percent_approved": round(100 * approved / total)}
    return DocumentSummary(document_id, unit_count=total, page_count=2, progress=counts, categories=categories or {})


def test_documents_are_grouped_into_folders_in_order_and_the_rest_stay_at_the_root():
    from app.services.categories import group_into_folders

    categories = [Category("VI", "Hộ kinh doanh", 6), Category("I", "Quản lý thuế", 1), Category("IX", "BHXH", 9)]
    items = [summary("ND125_2020", {"I": 20}, approved=10), summary("L108_2025_QH15", {"I": 10}, approved=5),
             summary("ND141_2026", {"I": 30, "VI": 10}, flagged=3), summary("ND20_2026"), summary("KHAC_2026", {"ZZ": 1})]

    folders, loose = group_into_folders(items, categories)
    assert [(f.category.code, [d.document_id for d in f.documents]) for f in folders] == [
        ("I", ["L108_2025_QH15", "ND125_2020", "ND141_2026"]), ("VI", ["ND141_2026"]), ("IX", [])]
    assert [d.document_id for d in loose] == ["ND20_2026", "KHAC_2026"]  # danh mục không còn: ở thư mục gốc
    first = folders[0]
    assert (first.unit_count, first.page_count) == (30, 6)
    assert first.progress == {"total": 30, "approved": 15, "flagged": 3, "structure_error": 0, "unreviewed": 12,
                              "percent_approved": 50}
    assert folders[2].progress is None and folders[2].unit_count == 0


def test_folders_without_review_progress_have_no_bar():
    from app.services.categories import group_into_folders
    from app.services.models import DocumentSummary

    folders, _ = group_into_folders([DocumentSummary("ND1_2026", unit_count=4, categories={"I": 1})],
                                    [Category("I", "Quản lý thuế", 1)])
    assert folders[0].progress is None and folders[0].unit_count == 4


def test_documents_follow_the_catalog_then_continuation_files_then_legal_hierarchy():
    catalog = {"L59_2020_QH14": 35, "L03_2022_QH15": 36, "ND168_2025": 38}
    stems = ["TT99_2025_BTC", "ND168_2025", "L59_2020_QH14_tiep", "L03_2022_QH15", "L59_2020_QH14", "ND20_2026",
             "L50_2024_QH15", "QD_KHAC"]
    ordered = sorted(stems, key=lambda stem: (position_in_folder(stem, catalog), stem))
    assert ordered == ["L59_2020_QH14", "L59_2020_QH14_tiep", "L03_2022_QH15", "ND168_2025",  # theo danh mục
                       "L50_2024_QH15", "ND20_2026", "TT99_2025_BTC", "QD_KHAC"]  # ngoài danh mục: Luật, NĐ, TT
