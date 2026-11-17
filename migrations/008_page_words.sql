-- Toạ độ từng từ do OCR trả về trên trang scan / ảnh chụp (app/services/page_words.py), để tô sáng đúng vùng của
-- Điều/Khoản/Điểm. Dữ liệu dẫn xuất: khóa theo sha256 của PDF (PDF đổi thì tự dùng bản mới), `engine` ghi cách OCR.
-- words: [[chữ, trái, trên, phải, dưới, số dòng], ...], toạ độ chuẩn hoá 0..1 theo trang.
CREATE TABLE IF NOT EXISTS page_words (
    pdf_sha256 text NOT NULL,
    page integer NOT NULL CHECK (page >= 1),
    engine text NOT NULL,
    words jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pdf_sha256, page, engine)
);
