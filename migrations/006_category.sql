-- Danh mục (thư mục chủ đề) giống cách bộ dữ liệu được sắp: "I. Quản lý thuế", "II. Hóa đơn điện tử"…
-- Một văn bản có thể thuộc nhiều danh mục (ND141 ở cả "V. Thuế TNDN" và "VI. Hộ kinh doanh").

CREATE TABLE category (
    id       bigserial PRIMARY KEY,
    code     text    NOT NULL UNIQUE,  -- "I", "II"… (số La Mã của thư mục); dùng trên URL
    name     text    NOT NULL,         -- "Quản lý thuế"
    position integer NOT NULL          -- thứ tự hiển thị: I = 1 … X = 10; thư mục không đánh số đứng sau
);

CREATE TABLE document_category (
    category_id bigint  NOT NULL REFERENCES category (id) ON DELETE CASCADE,
    document_pk bigint  NOT NULL REFERENCES document (id) ON DELETE CASCADE,
    rank        integer NOT NULL,      -- thứ tự trong danh mục (theo STT của tệp danh mục Excel)
    PRIMARY KEY (category_id, document_pk)
);
CREATE INDEX document_category_document ON document_category (document_pk);

-- Nhận ra PDF trùng byte với văn bản đã nhập (TT52_2025_BTC = TT152_2025_BTC) mà không phải tách/OCR lại
CREATE INDEX document_pdf_sha256 ON document_pdf (sha256);
