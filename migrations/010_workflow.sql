-- Quy trình làm việc của văn bản (app/services/workflow.py): Admin phân công người chỉnh sửa và người phê duyệt; người
-- chỉnh sửa bấm Hoàn thành; người phê duyệt Trả lại (kèm ghi chú) hoặc Hoàn tất phê duyệt; Admin mở lại.
-- CHỈ GHI THÊM như review_event: trạng thái luôn tính lại từ các dòng này. Không khóa ngoại tới document: nhập lại
-- văn bản với --replace không làm mất phân công.
CREATE TABLE IF NOT EXISTS workflow_event (
    id          bigserial   PRIMARY KEY,
    document_id text        NOT NULL,
    action      text        NOT NULL CHECK (action IN ('assign', 'unassign', 'submit', 'reopen', 'return', 'finalize')),
    actor       text        NOT NULL CHECK (length(btrim(actor)) > 0),
    target_user text,
    target_role text        CHECK (target_role IN ('editor', 'approver')),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (CASE
        WHEN action IN ('assign', 'unassign') THEN target_user IS NOT NULL AND target_role IS NOT NULL
        WHEN action = 'reopen' THEN target_user IS NULL OR target_role = 'editor'
        ELSE target_user IS NULL AND target_role IS NULL
    END),
    CHECK (action <> 'return' OR length(btrim(coalesce(note, ''))) > 0)
);

CREATE INDEX IF NOT EXISTS workflow_event_document_idx ON workflow_event (document_id, id);

-- Hàm trigger dùng chung cho các bảng chỉ ghi thêm
CREATE OR REPLACE FUNCTION append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% chỉ được ghi thêm, không sửa hay xóa', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workflow_event_no_update_delete ON workflow_event;
CREATE TRIGGER workflow_event_no_update_delete
    BEFORE UPDATE OR DELETE ON workflow_event
    FOR EACH ROW EXECUTE FUNCTION append_only();
