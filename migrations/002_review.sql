-- Lịch sử thẩm định: CHỈ GHI THÊM. Trạng thái hiện tại của một đơn vị luôn tính lại từ các dòng này.
-- Không khóa ngoại tới legal_unit/document: nhập lại văn bản với --replace không làm mất lịch sử.
CREATE TABLE review_event (
    id             bigserial PRIMARY KEY,
    document_id    text        NOT NULL,
    unit_id        text        NOT NULL,
    action         text        NOT NULL CHECK (action IN ('approve', 'edit', 'flag', 'structure_error', 'reset')),
    reviewer       text        NOT NULL CHECK (length(btrim(reviewer)) > 0),
    note           text,
    content_before text,       -- chỉ có ở action = 'edit'
    content_after  text,       -- chỉ có ở action = 'edit'
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK ((action = 'edit') = (content_after IS NOT NULL))
);

CREATE INDEX review_event_unit_idx ON review_event (document_id, unit_id, id);

CREATE FUNCTION review_event_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'review_event chỉ được ghi thêm, không sửa hay xóa';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER review_event_no_update_delete
    BEFORE UPDATE OR DELETE ON review_event
    FOR EACH ROW EXECUTE FUNCTION review_event_append_only();
