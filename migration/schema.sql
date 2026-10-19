-- SQLite/libSQL only. Never run against the existing PostgreSQL project.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS application_maintenance (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), active INTEGER NOT NULL CHECK(active IN (0,1))
);
INSERT OR IGNORE INTO application_maintenance VALUES(1,1);
CREATE TABLE IF NOT EXISTS dataset_revision(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL);
INSERT OR IGNORE INTO dataset_revision VALUES(1,0);
CREATE TABLE IF NOT EXISTS app_members (
 firebase_uid TEXT PRIMARY KEY, legacy_user_id TEXT UNIQUE, email TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','admin','master')),
 approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
 disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)),
 tokens_valid_after INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_login_history (
 session_key TEXT PRIMARY KEY, firebase_uid TEXT NOT NULL,
 email TEXT NOT NULL, actor_role TEXT NOT NULL CHECK(actor_role IN ('user','admin','master')),
 auth_time INTEGER NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS viewer_logins ON app_login_history(actor_role,auth_time DESC,session_key);
CREATE TABLE IF NOT EXISTS access_logs (
 id INTEGER PRIMARY KEY,firebase_uid TEXT NOT NULL,action TEXT NOT NULL,
 details_json TEXT NOT NULL CHECK(json_valid(details_json)),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS member_actions (
 id INTEGER PRIMARY KEY,actor_uid TEXT NOT NULL,target_uid TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','admin')),
 approved INTEGER NOT NULL CHECK(approved IN (0,1)),disabled INTEGER NOT NULL CHECK(disabled IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS checked_member_action BEFORE INSERT ON member_actions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM app_members WHERE firebase_uid=NEW.actor_uid AND approved=1 AND disabled=0 AND role IN ('admin','master'))
 THEN RAISE(ABORT,'admin required') END;
 SELECT CASE WHEN NEW.actor_uid=NEW.target_uid OR NOT EXISTS(SELECT 1 FROM app_members WHERE firebase_uid=NEW.target_uid AND role<>'master')
 THEN RAISE(ABORT,'protected or unknown member') END;
END;
CREATE TRIGGER IF NOT EXISTS apply_member_action AFTER INSERT ON member_actions BEGIN
 UPDATE app_members SET role=NEW.role,approved=NEW.approved,disabled=NEW.disabled,
 tokens_valid_after=CASE WHEN NEW.approved=0 OR NEW.disabled=1 THEN max(tokens_valid_after,unixepoch()+1) ELSE tokens_valid_after END
 WHERE firebase_uid=NEW.target_uid;
END;
CREATE TABLE IF NOT EXISTS notification_snapshots (
 id TEXT PRIMARY KEY, bureau TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'staging'
 CHECK(state IN ('staging','validated')), reference_dates TEXT NOT NULL CHECK(json_valid(reference_dates)),
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 expected_facilities INTEGER NOT NULL, expected_filings INTEGER NOT NULL,
 UNIQUE(id,bureau)
);
CREATE TABLE IF NOT EXISTS notification_current (
 bureau TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
 FOREIGN KEY(snapshot_id,bureau) REFERENCES notification_snapshots(id,bureau)
);
CREATE TRIGGER IF NOT EXISTS current_validated_insert BEFORE INSERT ON notification_current
WHEN NOT EXISTS(SELECT 1 FROM notification_snapshots WHERE id=NEW.snapshot_id AND state='validated')
BEGIN SELECT RAISE(ABORT,'validated snapshot required'); END;
CREATE TRIGGER IF NOT EXISTS current_validated_update BEFORE UPDATE ON notification_current
WHEN NOT EXISTS(SELECT 1 FROM notification_snapshots WHERE id=NEW.snapshot_id AND state='validated')
BEGIN SELECT RAISE(ABORT,'validated snapshot required'); END;
CREATE TRIGGER IF NOT EXISTS current_cannot_unvalidate BEFORE UPDATE OF state ON notification_snapshots
WHEN NEW.state<>'validated' AND EXISTS(SELECT 1 FROM notification_current WHERE snapshot_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'current snapshot must remain validated'); END;
CREATE TABLE IF NOT EXISTS notification_facilities (
 snapshot_id TEXT NOT NULL REFERENCES notification_snapshots(id),code TEXT NOT NULL,
 prefecture_code INTEGER NOT NULL CHECK(prefecture_code BETWEEN 1 AND 47),reference_date TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,code)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS notification_items (
 snapshot_id TEXT NOT NULL REFERENCES notification_snapshots(id),item_key TEXT NOT NULL,
 official_item_name TEXT NOT NULL, PRIMARY KEY(snapshot_id,item_key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS notification_filings (
 snapshot_id TEXT NOT NULL,code TEXT NOT NULL,item_key TEXT NOT NULL,
 date_basis TEXT NOT NULL CHECK(date_basis IN ('receipt_date','calculation_start')),
 receipt_date TEXT,calculation_start TEXT,payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 PRIMARY KEY(snapshot_id,code,item_key),
 FOREIGN KEY(snapshot_id,code) REFERENCES notification_facilities(snapshot_id,code),
 FOREIGN KEY(snapshot_id,item_key) REFERENCES notification_items(snapshot_id,item_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS filing_item_code ON notification_filings(snapshot_id,item_key,code);
CREATE TABLE IF NOT EXISTS notification_jobs (
 month TEXT NOT NULL,bureau TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('success','failed')),
 attempted_at TEXT,details_json TEXT NOT NULL CHECK(json_valid(details_json)), PRIMARY KEY(month,bureau)
);
CREATE TRIGGER IF NOT EXISTS notification_revision_insert AFTER INSERT ON notification_current
BEGIN UPDATE dataset_revision SET revision=revision+1 WHERE singleton=1; END;
CREATE TRIGGER IF NOT EXISTS notification_revision_update AFTER UPDATE ON notification_current
BEGIN UPDATE dataset_revision SET revision=revision+1 WHERE singleton=1; END;
