"""API JSON dưới /api. Mở /docs để xem và thử từng endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.contracts import LegalUnit
from app.schemas import (
    DocumentOut, DocumentSummaryOut, ImportResultOut, ProgressOut, ReviewEventOut, ReviewRequest, ReviewResultOut,
)
from app.services.importer import AlreadyImported
from app.services.review import ReviewProblem, build_export, build_ocr_report, progress
from app.services.tree import build_tree

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

router = APIRouter(prefix="/api", tags=["api"])


def get_catalog(request: Request):
    return request.app.state.catalog


def load_or_404(catalog, document_id: str):
    try:
        return catalog.get(document_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản này")


def _progress(loaded):
    return progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews) if loaded.can_review else None


@router.get("/documents", response_model=list[DocumentSummaryOut], response_model_exclude_none=True, summary="Danh sách văn bản")
def list_documents(catalog=Depends(get_catalog)):
    return [
        DocumentSummaryOut(
            document_id=s.document_id,
            document_no=s.document_no,
            document_type=s.document_type,
            title=s.title,
            page_count=s.page_count,
            unit_count=s.unit_count,
            progress=s.progress,
        )
        for s in catalog.list_documents()
        if s.problem is None
    ]


@router.get("/documents/{document_id}", response_model=DocumentOut, summary="Chi tiết văn bản kèm cây Điều/Khoản/Điểm")
def get_document(document_id: str, catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id)
    doc = loaded.doc
    return DocumentOut(
        document_id=doc.document_id,
        document_no=doc.document_no,
        document_type=doc.document_type,
        title=doc.title,
        page_count=loaded.page_count,
        unit_count=len(doc.legal_units),
        progress=_progress(loaded),
        issuer=doc.issuer,
        issued_date=doc.issued_date,
        effective_from=doc.effective_from,
        effective_to=doc.effective_to,
        status=doc.status,
        amended_by=doc.amended_by,
        replaced_by=doc.replaced_by,
        tree=build_tree(doc.legal_units, loaded.reviews),
    )


@router.get("/documents/{document_id}/units/{unit_id}", response_model=LegalUnit, summary="Một đơn vị, đủ nội dung")
def get_unit(document_id: str, unit_id: str, catalog=Depends(get_catalog)):
    unit = load_or_404(catalog, document_id).units_by_id.get(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Không có đơn vị này")
    return unit


@router.get(
    "/documents/{document_id}/pages/{page}",
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}}},
    summary="Ảnh trang thứ N (đếm từ 1) của văn bản",
)
def get_page_image(document_id: str, page: int, catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id)
    if page < 1 or page > loaded.page_count:
        raise HTTPException(status_code=404, detail=f"Văn bản có {loaded.page_count} trang")
    try:
        image = loaded.page_image(page)
    except Exception as exc:  # thiếu source.pdf, PDF hỏng, thiếu quyền ghi...
        raise HTTPException(status_code=500, detail=f"Không render được trang {page}: {exc}")
    return FileResponse(image, media_type="image/png")


@router.post(
    "/documents",
    response_model=ImportResultOut,
    status_code=201,
    summary="Nhập một văn bản (PDF gốc + JSON LegalDocument)",
    responses={
        200: {"description": "Đã có văn bản y hệt, không làm gì"},
        409: {"description": "Đã có văn bản khác nội dung cùng document_id; gửi replace=true để thay"},
        422: {"description": "JSON hoặc PDF sai; danh sách lỗi cụ thể nằm trong `problems`"},
        501: {"description": "Chưa cấu hình PostgreSQL"},
    },
)
def import_document(
    request: Request,
    response: Response,
    pdf: UploadFile = File(..., description="PDF gốc"),
    document: UploadFile = File(..., description="document.json theo LegalDocument"),
    replace: bool = Form(False, description="Thay bản cũ nếu đã có cùng document_id"),
):
    importer = request.app.state.importer
    if importer is None:
        raise HTTPException(status_code=501, detail="Nhập văn bản cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)")
    pdf_bytes = pdf.file.read(MAX_UPLOAD_BYTES + 1)
    json_bytes = document.file.read(MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > MAX_UPLOAD_BYTES or len(json_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Tệp quá lớn (tối đa 50 MB mỗi tệp)")
    try:
        result = importer.import_bytes(pdf_bytes, json_bytes, replace=replace)
    except AlreadyImported as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Đã có văn bản {exc.document_id} với nội dung khác. Gửi replace=true để thay bản cũ.",
        )
    if result.status == "unchanged":
        response.status_code = 200
    return ImportResultOut(**result.__dict__)


# ---- thẩm định ----

@router.get("/documents/{document_id}/progress", response_model=ProgressOut, summary="Tiến độ thẩm định của văn bản")
def get_progress(document_id: str, catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id)
    return progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)


@router.get(
    "/documents/{document_id}/units/{unit_id}/reviews",
    response_model=list[ReviewEventOut],
    summary="Lịch sử thẩm định của một đơn vị (mới nhất trước)",
)
def get_unit_reviews(document_id: str, unit_id: str, catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id)
    if unit_id not in loaded.units_by_id:
        raise HTTPException(status_code=404, detail="Không có đơn vị này")
    return [ReviewEventOut(**e.__dict__) for e in reversed(loaded.events_of(unit_id))]


@router.post(
    "/documents/{document_id}/units/{unit_id}/reviews",
    response_model=ReviewResultOut,
    status_code=201,
    summary="Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc hoặc Bỏ đánh dấu một đơn vị",
    responses={
        422: {"description": "Thao tác không hợp lệ (thiếu tên người soát, thiếu lý do, nội dung không đổi...); xem `problems`"},
        501: {"description": "Cần PostgreSQL để lưu thao tác"},
    },
)
def post_review(document_id: str, unit_id: str, body: ReviewRequest, catalog=Depends(get_catalog)):
    if not hasattr(catalog, "record_review"):
        raise HTTPException(status_code=501, detail="Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)")
    try:
        event = catalog.record_review(document_id, unit_id, body.action, body.reviewer, body.note, body.content)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản hoặc đơn vị này")
    loaded = catalog.get(document_id)
    return ReviewResultOut(
        event=ReviewEventOut(**event.__dict__),
        review_status=loaded.reviews[unit_id].status,
        progress=progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews),
    )


@router.get(
    "/documents/{document_id}/export",
    summary="Xuất JSON các đơn vị đã duyệt (kèm bản sửa chữ)",
    response_class=JSONResponse,
)
def export_approved(document_id: str, catalog=Depends(get_catalog)):
    """Tệp tải về có cùng các trường như LegalDocument, chỉ gồm đơn vị đã duyệt, nội dung là bản hiện hành.
    Header `X-Export-Units` cho biết số đơn vị đã xuất / tổng; `X-Export-Parent-Missing` là số đơn vị đã duyệt mà đơn vị cha chưa duyệt."""
    loaded = load_or_404(catalog, document_id)
    if not loaded.can_review:
        raise HTTPException(status_code=501, detail="Xuất dữ liệu đã duyệt cần PostgreSQL")
    data, stats = build_export(loaded.doc, loaded.reviews)
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": f'attachment; filename="{document_id}_approved.json"',
            "X-Export-Units": f"{stats['exported']}/{stats['total']}",
            "X-Export-Parent-Missing": str(len(stats["parent_not_exported"])),
        },
    )


@router.get("/documents/{document_id}/ocr-report", summary="Báo cáo lỗi ký tự OCR, nguyên nhân và độ chính xác (%)")
def get_ocr_report(document_id: str, catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id)
    return build_ocr_report(loaded.doc, loaded.reviews)

