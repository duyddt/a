"""API JSON dưới /api. Mở /docs để xem và thử từng endpoint."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.contracts import LegalUnit
from app.schemas import (
    CategoryOut,
    CategoryRefOut,
    DocumentOut,
    DocumentStatsOut,
    DocumentSummaryOut,
    ImportResultOut,
    PersonStatsOut,
    ProgressOut,
    ReviewEventOut,
    ReviewRequest,
    ReviewResultOut,
    StatsOut,
    VersionOut,
    WorkflowEditorOut,
    WorkflowOut,
    WorkflowRequest,
)
from app.services.categories import Category, group_into_folders
from app.services.importer import AlreadyImported
from app.services.models import PERSON_COLORS, ImportProblem
from app.services.review import build_export, build_ocr_report, progress
from app.services.tree import build_tree
from app.services.views import unit_versions
from app.web_auth import current_user, require_admin, require_reviewer

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
log = logging.getLogger("review-app")

router = APIRouter(prefix="/api", tags=["api"])


def get_catalog(request: Request):
    return request.app.state.catalog


VERSION_HELP = ("Bản muốn xem: final = bản chốt, hoặc tên một người chỉnh sửa (chỉ người phê duyệt / Admin). "
                "Người chỉnh sửa luôn nhận bản của chính mình.")


def load_or_404(catalog, document_id: str, viewer: dict | None = None, version: str | None = None):
    """Văn bản theo bản của `viewer` (None = bản chốt); 404 nếu không có."""
    try:
        return catalog.get(document_id, viewer=viewer, version=version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản này") from None


def sees_every_version(user: dict | None) -> bool:
    return bool(user) and user.get("role") in ("approver", "admin")


def workflow_out(state) -> WorkflowOut:
    return WorkflowOut(
        document_id=state.document_id,
        status=state.status,
        status_label=state.status_label,
        editors=[WorkflowEditorOut(username=name, submitted=done, submitted_at=state.submitted_at.get(name),
                                   color=index % PERSON_COLORS + 1)
                 for index, (name, done) in enumerate(state.editors.items())],
        approver=state.approver,
        returned_note=state.returned_note,
        returned_by=state.returned_by,
        finalized_by=state.finalized_by,
        finalized_at=state.finalized_at,
        updated_at=state.updated_at,
    )


def _workflow_catalog(catalog):
    if not hasattr(catalog, "record_workflow"):
        raise HTTPException(status_code=501, detail="Phân công và phê duyệt cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)")
    return catalog


@router.get("/stats", response_model=StatsOut, summary="Thống kê tiến độ từng người và từng văn bản (chỉ Admin)")
def get_stats(request: Request, folder: str | None = Query(None, description="Chỉ tính văn bản trong thư mục này (ví dụ II)"),
              catalog=Depends(get_catalog)):
    require_admin(request)
    stats = _workflow_catalog(catalog).stats(folder)
    return StatsOut(
        total_documents=stats.total_documents,
        status_counts=stats.status_counts,
        people=[PersonStatsOut(**{k: getattr(p, k) for k in PersonStatsOut.model_fields}) for p in stats.people],
        documents=[DocumentStatsOut(**{k: getattr(d, k) for k in DocumentStatsOut.model_fields}) for d in stats.documents],
    )


@router.get("/documents/{document_id}/workflow", response_model=WorkflowOut,
            summary="Phân công và trạng thái quy trình của văn bản")
def get_workflow(document_id: str, catalog=Depends(get_catalog)):
    try:
        return workflow_out(_workflow_catalog(catalog).workflow(document_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản này") from None


@router.post("/documents/{document_id}/workflow", response_model=WorkflowOut,
             summary="Hoàn thành, Trả lại, Hoàn tất phê duyệt; Admin: phân công, gỡ phân công, mở lại")
def post_workflow(request: Request, document_id: str, body: WorkflowRequest, catalog=Depends(get_catalog)):
    """Văn bản chuyển sang người phê duyệt ngay khi mọi người chỉnh sửa được giao đã bấm Hoàn thành. Thao tác ngoài
    quyền hoặc sai trạng thái trả 422 kèm `problems`."""
    catalog = _workflow_catalog(catalog)
    user = require_reviewer(request)
    try:
        catalog.record_workflow(document_id, body.action, user, body.note, body.target_user, body.target_role)
        return workflow_out(catalog.workflow(document_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản này") from None


def _progress(loaded):
    return progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews) if loaded.can_review else None


@router.get("/documents", response_model=list[DocumentSummaryOut], response_model_exclude_none=True, summary="Danh sách văn bản")
def list_documents(request: Request, catalog=Depends(get_catalog)):
    """Tiến độ đếm trên bản của người gọi (người chỉnh sửa: bản riêng; người phê duyệt / Admin: bản chốt)."""
    categories = {category.code: category for category in catalog.list_categories()}
    return [
        DocumentSummaryOut(
            document_id=s.document_id,
            document_no=s.document_no,
            document_type=s.document_type,
            title=s.title,
            page_count=s.page_count,
            unit_count=s.unit_count,
            progress=s.progress,
            categories=[
                CategoryRefOut(code=c.code, name=c.name)
                for c in sorted((categories[code] for code in s.categories if code in categories), key=Category.sort_key)
            ],
        )
        for s in catalog.list_documents(viewer=current_user(request))
        if s.problem is None
    ]


@router.get("/categories", response_model=list[CategoryOut], summary="Danh mục (thư mục chủ đề) và văn bản của từng danh mục")
def list_categories(catalog=Depends(get_catalog)):
    folders, _ = group_into_folders([s for s in catalog.list_documents() if s.problem is None], catalog.list_categories())
    return [
        CategoryOut(code=f.category.code, name=f.category.name, position=f.category.position,
                    document_count=len(f.documents), documents=[d.document_id for d in f.documents])
        for f in folders
    ]


@router.get("/documents/{document_id}", response_model=DocumentOut, summary="Chi tiết văn bản kèm cây Điều/Khoản/Điểm")
def get_document(request: Request, document_id: str, version: str | None = Query(None, description=VERSION_HELP),
                 catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id, current_user(request), version)
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
def get_unit(request: Request, document_id: str, unit_id: str, version: str | None = Query(None, description=VERSION_HELP),
             catalog=Depends(get_catalog)):
    unit = load_or_404(catalog, document_id, current_user(request), version).units_by_id.get(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Không có đơn vị này")
    return unit


@router.get(
    "/documents/{document_id}/units/{unit_id}/versions",
    response_model=list[VersionOut],
    summary="Các bản của một đơn vị: OCR gốc, bản của từng người chỉnh sửa, bản chốt",
)
def get_unit_versions(request: Request, document_id: str, unit_id: str, catalog=Depends(get_catalog)):
    """Người phê duyệt / Admin nhận mọi bản; người chỉnh sửa chỉ nhận bản OCR gốc và bản của mình."""
    user = current_user(request)
    loaded = load_or_404(catalog, document_id, user)
    if unit_id not in loaded.units_by_id:
        raise HTTPException(status_code=404, detail="Không có đơn vị này")
    return [VersionOut(**v) for v in unit_versions(loaded, unit_id, sees_every_version(user))]


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
    except Exception:  # thiếu source.pdf, PDF hỏng, thiếu quyền ghi...
        log.exception("Không render được trang %s của %s", page, document_id)
        raise HTTPException(status_code=500, detail=f"Không render được trang {page}") from None
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
    document: UploadFile | None = File(None, description="document.json (nếu bỏ trống sẽ tự trích xuất từ PDF)"),
    replace: bool = Form(False, description="Thay bản cũ nếu đã có cùng document_id"),
    category: str | None = Form(None, description="Mã danh mục đã có để xếp văn bản vào (ví dụ II); bỏ trống = không xếp"),
):
    importer = request.app.state.importer
    if importer is None:
        raise HTTPException(status_code=501, detail="Nhập văn bản cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)")
    require_admin(request)  # chỉ Admin thêm hoặc ghi đè văn bản
    pdf_bytes = pdf.file.read(MAX_UPLOAD_BYTES + 1)
    json_bytes = document.file.read(MAX_UPLOAD_BYTES + 1) if (document and document.filename) else b''
    if len(pdf_bytes) > MAX_UPLOAD_BYTES or len(json_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Tệp quá lớn (tối đa 50 MB mỗi tệp)")
    words: dict = {}  # toạ độ chữ của các trang phải OCR, lưu lại để tô sáng trên bản scan
    if not json_bytes.strip():
        from app.services.pdf_parser import auto_build_json_from_pdf

        try:
            json_bytes = auto_build_json_from_pdf(pdf_bytes, pdf.filename or "document.pdf", words_out=words)
        except ImportProblem:
            raise  # đã có lý do cụ thể (ví dụ PDF scan chưa OCR được)
        except Exception as exc:
            raise ImportProblem(pdf.filename or "document", [f"Không tự tách được cấu trúc từ PDF: {type(exc).__name__}"]) from exc
    try:
        result = importer.import_bytes(pdf_bytes, json_bytes, replace=replace, category=(category or "").strip() or None,
                                       page_words=words)
    except AlreadyImported as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Đã có văn bản {exc.document_id} với nội dung khác. Gửi replace=true để thay bản cũ.",
        ) from exc
    if result.status == "unchanged":
        response.status_code = 200
    return ImportResultOut(**result.__dict__)


# ---- thẩm định ----

@router.get("/documents/{document_id}/progress", response_model=ProgressOut, summary="Tiến độ thẩm định của văn bản")
def get_progress(request: Request, document_id: str, version: str | None = Query(None, description=VERSION_HELP),
                 catalog=Depends(get_catalog)):
    loaded = load_or_404(catalog, document_id, current_user(request), version)
    return progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)


@router.get(
    "/documents/{document_id}/units/{unit_id}/reviews",
    response_model=list[ReviewEventOut],
    summary="Lịch sử thẩm định của một đơn vị (mới nhất trước)",
)
def get_unit_reviews(request: Request, document_id: str, unit_id: str,
                     version: str | None = Query(None, description=VERSION_HELP + " all = mọi bản."),
                     catalog=Depends(get_catalog)):
    user = current_user(request)
    loaded = load_or_404(catalog, document_id, user, version)
    if unit_id not in loaded.units_by_id:
        raise HTTPException(status_code=404, detail="Không có đơn vị này")
    events = loaded.events_of(unit_id)
    if version == "all" and sees_every_version(user) and loaded.tracks is not None:
        tracks = loaded.tracks
        everything = tracks.final_events + [e for evs in tracks.editor_events.values() for e in evs]
        events = sorted((e for e in everything if e.unit_id == unit_id), key=lambda e: e.id)
    return [ReviewEventOut(**e.__dict__) for e in reversed(events)]


@router.post(
    "/documents/{document_id}/units/{unit_id}/reviews",
    response_model=ReviewResultOut,
    status_code=201,
    summary="Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc, Bỏ đánh dấu hoặc Chọn bản cho một đơn vị",
    responses={
        422: {"description": "Thao tác không hợp lệ (thiếu tên người soát, thiếu lý do, nội dung không đổi...); xem `problems`"},
        501: {"description": "Cần PostgreSQL để lưu thao tác"},
    },
)
def post_review(request: Request, document_id: str, unit_id: str, body: ReviewRequest, catalog=Depends(get_catalog)):
    """Người soát được ghi nhận là tài khoản đã xác thực (trường `reviewer` trong body bị bỏ qua).

    Người chỉnh sửa ghi vào bản riêng của mình; người phê duyệt và Admin ghi vào bản chốt. `adopt` (chọn bản) chép chữ
    của bản `source` ("ocr" hoặc tên người chỉnh sửa) vào bản chốt."""
    if not hasattr(catalog, "record_review"):
        raise HTTPException(status_code=501, detail="Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)")
    user = require_reviewer(request)
    try:
        event = catalog.record_review(document_id, unit_id, body.action, user["username"], body.note, body.content,
                                      role=user["role"], source=body.source)
    except KeyError:
        raise HTTPException(status_code=404, detail="Không có văn bản hoặc đơn vị này") from None
    loaded = catalog.get(document_id, viewer=user)
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
    """Tệp tải về có cùng các trường như LegalDocument, chỉ gồm đơn vị đã duyệt TRÊN BẢN CHỐT, nội dung là bản chốt.
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
    """Tính trên bản chốt (chuẩn đối chiếu)."""
    loaded = load_or_404(catalog, document_id)
    return build_ocr_report(loaded.doc, loaded.reviews)

