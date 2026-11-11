ALTER TABLE reviewer_account ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'viewer';
ALTER TABLE reviewer_account ADD COLUMN IF NOT EXISTS is_approved boolean NOT NULL DEFAULT false;
