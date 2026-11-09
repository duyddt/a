-- Tài khoản người soát và lưu vị trí tiến trình duyệt theo từng người
CREATE TABLE IF NOT EXISTS reviewer_account (
    username     text        PRIMARY KEY CHECK (length(btrim(username)) > 0),
    display_name text        NOT NULL,
    password_hash text       NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviewer_progress (
    username     text        NOT NULL,
    document_id  text        NOT NULL,
    last_unit_id text        NOT NULL,
    last_page    integer     NOT NULL DEFAULT 1,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (username, document_id)
);
