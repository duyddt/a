-- Văn bản pháp luật và các đơn vị (Điều/Khoản/Điểm). Áp dụng tự động khi API khởi động (app/db.py: migrate).
-- Mỗi file trong thư mục này chạy đúng một lần, theo thứ tự tên; không sửa file đã áp dụng, hãy thêm file mới.

CREATE TABLE document (
    id             bigserial PRIMARY KEY,
    document_id    text        NOT NULL UNIQUE,          -- theo make_document_id, ví dụ ND254_2026
    document_no    text        NOT NULL,
    document_type  text        NOT NULL,
    title          text        NOT NULL,
    issuer         text,
    issued_date    date,
    effective_from date,
    effective_to   date,                                  -- nửa mở [from, to)
    status         text        NOT NULL,
    amended_by     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    replaced_by    text,
    page_count     integer     NOT NULL CHECK (page_count > 0),
    content_hash   text        NOT NULL,                  -- sha256(PDF + JSON chuẩn hóa), để nhập lại không tạo bản trùng
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE legal_unit (
    id             bigserial PRIMARY KEY,
    document_pk    bigint      NOT NULL REFERENCES document (id) ON DELETE CASCADE,
    position       integer     NOT NULL,                  -- thứ tự trong legal_units của file (cha đứng trước con)
    unit_id        text        NOT NULL,
    parent_unit_id text,
    chapter        text,
    section        text,
    article        integer,
    clause         integer,
    point          text,
    title          text,
    content        text        NOT NULL,
    effective_from date,
    effective_to   date,
    status         text        NOT NULL,
    is_confident   boolean     NOT NULL DEFAULT true,
    source_pages   integer[]   NOT NULL DEFAULT '{}',     -- số trang PDF (đếm từ 1) chứa đơn vị này
    UNIQUE (document_pk, unit_id),
    UNIQUE (document_pk, position)
);
