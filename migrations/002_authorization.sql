-- 传播授权领域模型
-- 约定：外部主体一律使用不含真实身份的引用编号；时间为带偏移量的 ISO 8601 文本；
-- 原始材料不入库，只保存受控引用（*_ref）与 sha256 指纹。

-- 非遗项目
CREATE TABLE IF NOT EXISTS heritage_projects (
    heritage_ref TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

-- 传承人允许展示的范围（scope 取值见 contracts/entities.json）
CREATE TABLE IF NOT EXISTS inheritor_consents (
    inheritor_ref     TEXT PRIMARY KEY,
    heritage_ref      TEXT NOT NULL,
    allowed_scopes    TEXT NOT NULL DEFAULT '', -- 逗号分隔，如 event-report,portrait
    consent_note      TEXT NOT NULL DEFAULT '',
    consented_at      TEXT NOT NULL,
    withdrawn_at      TEXT,
    FOREIGN KEY (heritage_ref) REFERENCES heritage_projects(heritage_ref)
);

-- 体验场次：活动发生过的事实，只追加、不删除（撤回许可不影响此行）
CREATE TABLE IF NOT EXISTS sessions (
    session_ref  TEXT PRIMARY KEY,
    heritage_ref TEXT NOT NULL,
    inheritor_ref TEXT NOT NULL,
    title        TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    location_ref TEXT NOT NULL DEFAULT '',
    fact_note    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (heritage_ref) REFERENCES heritage_projects(heritage_ref),
    FOREIGN KEY (inheritor_ref) REFERENCES inheritor_consents(inheritor_ref)
);

-- 场次参与者（体验者、青年创作者、口译员等），portrait_consent 为肖像同意
CREATE TABLE IF NOT EXISTS participants (
    participant_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_ref      TEXT NOT NULL,
    participant_ref  TEXT NOT NULL,
    role             TEXT NOT NULL,
    portrait_consent INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    UNIQUE (session_ref, participant_ref),
    FOREIGN KEY (session_ref) REFERENCES sessions(session_ref)
);

-- 材料包
CREATE TABLE IF NOT EXISTS material_kits (
    material_kit_ref TEXT PRIMARY KEY,
    heritage_ref     TEXT NOT NULL,
    name             TEXT NOT NULL,
    contents_summary TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    FOREIGN KEY (heritage_ref) REFERENCES heritage_projects(heritage_ref)
);

-- 青年体验作品
CREATE TABLE IF NOT EXISTS youth_works (
    work_ref                TEXT PRIMARY KEY,
    session_ref             TEXT NOT NULL,
    creator_participant_ref TEXT NOT NULL,
    material_kit_ref        TEXT,
    title                   TEXT NOT NULL,
    public_summary          TEXT NOT NULL DEFAULT '',
    display_permission      INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (session_ref) REFERENCES sessions(session_ref),
    FOREIGN KEY (material_kit_ref) REFERENCES material_kits(material_kit_ref)
);

-- 工序文档（关键工序只公开 public_summary）
CREATE TABLE IF NOT EXISTS process_documents (
    doc_ref       TEXT PRIMARY KEY,
    heritage_ref  TEXT NOT NULL,
    session_ref   TEXT,
    stage_key     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL,
    is_key_process INTEGER NOT NULL DEFAULT 0,
    public_summary TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (heritage_ref) REFERENCES heritage_projects(heritage_ref),
    FOREIGN KEY (session_ref) REFERENCES sessions(session_ref)
);

-- 原文修订版（seq 从 1 递增）
CREATE TABLE IF NOT EXISTS document_revisions (
    doc_ref      TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    source_ref   TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    change_note  TEXT NOT NULL DEFAULT '',
    revised_at   TEXT NOT NULL,
    PRIMARY KEY (doc_ref, seq),
    FOREIGN KEY (doc_ref) REFERENCES process_documents(doc_ref)
);

-- 译文修订版：按 (doc_ref, seq) 与原文一一配对，不允许出现无译文对应的原文改动
CREATE TABLE IF NOT EXISTS translation_revisions (
    doc_ref           TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    language          TEXT NOT NULL,
    translation_ref   TEXT NOT NULL,
    translation_sha256 TEXT NOT NULL,
    translator_ref    TEXT NOT NULL DEFAULT '',
    revised_at        TEXT NOT NULL,
    PRIMARY KEY (doc_ref, seq, language),
    FOREIGN KEY (doc_ref, seq) REFERENCES document_revisions(doc_ref, seq)
);

-- 媒体素材：同一指纹只对应同一素材（重复上传识别为同一 asset）
CREATE TABLE IF NOT EXISTS media_assets (
    asset_ref         TEXT PRIMARY KEY,
    fingerprint_sha256 TEXT NOT NULL UNIQUE,
    media_type        TEXT NOT NULL DEFAULT 'video',
    title             TEXT NOT NULL,
    source_ref        TEXT NOT NULL DEFAULT '',
    captured_at       TEXT,
    created_at        TEXT NOT NULL
);

-- 上传记录：同一素材可被不同媒体多次上传，形成多条 capture
CREATE TABLE IF NOT EXISTS media_captures (
    capture_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_ref    TEXT NOT NULL,
    uploader_org TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (asset_ref) REFERENCES media_assets(asset_ref)
);

-- 素材与项目/场次/传承人/参与者/作品/文档/材料包的可追查关联
CREATE TABLE IF NOT EXISTS asset_links (
    asset_ref   TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN
        ('heritage','session','inheritor','participant','work','document','material_kit')),
    target_ref  TEXT NOT NULL,
    PRIMARY KEY (asset_ref, target_kind, target_ref),
    FOREIGN KEY (asset_ref) REFERENCES media_assets(asset_ref)
);

-- 媒体用途许可
CREATE TABLE IF NOT EXISTS media_grants (
    grant_ref    TEXT PRIMARY KEY,
    asset_ref    TEXT NOT NULL,
    grantee_org  TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN
        ('event-report','full-process','portrait','internal-archive')),
    purpose_note TEXT NOT NULL DEFAULT '',
    authority    TEXT NOT NULL DEFAULT '',
    granted_at   TEXT NOT NULL,
    valid_from   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    revoked_at   TEXT,
    revoke_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (asset_ref) REFERENCES media_assets(asset_ref)
);
CREATE INDEX IF NOT EXISTS idx_grants_lookup
    ON media_grants(asset_ref, grantee_org, status);

-- 媒体报道：required_scopes 为该报道实际使用到的边界
CREATE TABLE IF NOT EXISTS reports (
    report_ref          TEXT PRIMARY KEY,
    grantee_org         TEXT NOT NULL,
    asset_ref           TEXT NOT NULL,
    title               TEXT NOT NULL,
    location_ref        TEXT NOT NULL DEFAULT '',
    required_scopes     TEXT NOT NULL DEFAULT 'event-report',
    published_at        TEXT,
    status              TEXT NOT NULL DEFAULT 'up' CHECK (status IN ('up','taken_down')),
    taken_down_at       TEXT,
    take_down_reason    TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    FOREIGN KEY (asset_ref) REFERENCES media_assets(asset_ref)
);

-- 展项：可展出素材、青年作品或工序文档
CREATE TABLE IF NOT EXISTS exhibits (
    exhibit_ref       TEXT PRIMARY KEY,
    grantee_org       TEXT NOT NULL,
    target_kind       TEXT NOT NULL CHECK (target_kind IN ('asset','work','document')),
    target_ref        TEXT NOT NULL,
    title             TEXT NOT NULL,
    venue_ref         TEXT NOT NULL DEFAULT '',
    required_scopes   TEXT NOT NULL DEFAULT 'event-report',
    status            TEXT NOT NULL DEFAULT 'up' CHECK (status IN ('up','taken_down')),
    taken_down_at     TEXT,
    take_down_reason  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

-- 审计事件：授权、撤回、拦截、下线等全部留痕
CREATE TABLE IF NOT EXISTS audit_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time  TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    entity_kind TEXT NOT NULL DEFAULT '',
    entity_ref  TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_authorization');
