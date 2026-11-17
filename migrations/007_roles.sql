-- Ba vai trò: admin (toàn quyền), approver (người phê duyệt), reviewer (người chỉnh sửa).
-- Bỏ vai trò 'viewer' và chế độ tự đăng ký: tài khoản viewer cũ thành reviewer, giữ nguyên is_approved
-- (viewer chưa được duyệt thành reviewer đang khóa). Từ đây is_approved nghĩa là "tài khoản đang hoạt động".
UPDATE reviewer_account SET role = 'reviewer' WHERE role NOT IN ('admin', 'approver', 'reviewer');
ALTER TABLE reviewer_account ALTER COLUMN role SET DEFAULT 'reviewer';
ALTER TABLE reviewer_account DROP CONSTRAINT IF EXISTS reviewer_account_role_check;
ALTER TABLE reviewer_account ADD CONSTRAINT reviewer_account_role_check CHECK (role IN ('admin', 'approver', 'reviewer'));
