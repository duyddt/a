-- PDF gốc của từng văn bản. Từ đây PostgreSQL là nơi lưu duy nhất: STORAGE_DIR chỉ còn là bộ đệm (source.pdf chép ra
-- từ bảng này và ảnh trang dựng khi có người xem), xóa lúc nào cũng được.
-- Bảng riêng (không thêm cột vào document) để các truy vấn danh sách không phải đọc kèm dữ liệu nhị phân.

CREATE TABLE document_pdf (
    document_pk bigint PRIMARY KEY REFERENCES document (id) ON DELETE CASCADE,
    sha256      text   NOT NULL,                          -- sha256 của pdf, để bộ đệm biết khi nào phải chép lại
    pdf         bytea  NOT NULL
);

-- PDF vốn đã nén: lưu thẳng ra TOAST, không tốn công nén lại
ALTER TABLE document_pdf ALTER COLUMN pdf SET STORAGE EXTERNAL;
