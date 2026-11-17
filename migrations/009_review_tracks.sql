-- Bản riêng của từng người (app/services/review.py: fold_tracks).
--   track = 'editor': bản riêng của người chỉnh sửa `reviewer`, độc lập với người khác.
--   track = 'final' : bản chốt chung của người phê duyệt và Admin.
-- Sự kiện có từ trước được coi là bản chốt: trước đây mọi người cùng làm trên một bản, và bản đó là bản được xuất.
-- Từ nay mỗi lần ghi phải nêu rõ track (cột không có giá trị mặc định).
ALTER TABLE review_event ADD COLUMN IF NOT EXISTS track text NOT NULL DEFAULT 'final';
ALTER TABLE review_event ALTER COLUMN track DROP DEFAULT;

-- Thao tác mới 'adopt' (chọn bản: chép chữ của bản OCR gốc hoặc bản của một người chỉnh sửa vào bản chốt).
-- Hai ràng buộc CHECK cũ không đặt tên: tìm theo nội dung rồi xóa.
DO $$
DECLARE c record;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'review_event'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%action%'
    LOOP
        EXECUTE format('ALTER TABLE review_event DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE review_event ADD CONSTRAINT review_event_action_check
    CHECK (action IN ('approve', 'edit', 'flag', 'structure_error', 'reset', 'adopt'));
ALTER TABLE review_event ADD CONSTRAINT review_event_content_check
    CHECK ((action IN ('edit', 'adopt')) = (content_after IS NOT NULL));
ALTER TABLE review_event ADD CONSTRAINT review_event_track_check
    CHECK (track IN ('editor', 'final') AND (action <> 'adopt' OR track = 'final'));

CREATE INDEX IF NOT EXISTS review_event_track_idx ON review_event (document_id, track, reviewer, id);
