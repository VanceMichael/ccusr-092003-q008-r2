-- 河湟非遗体验传播授权：核心领域模型
-- 所有业务主体使用不含真实身份的引用编号；时间为带偏移量 ISO 8601；
-- 原始材料不落库，只保存受控引用与 sha256 摘要。

-- 非遗项目
CREATE TABLE IF NOT EXISTS heritage_projects (
    ref            TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    public_summary TEXT NOT NULL,          -- 面向公众的获准说明
    created_at     TEXT NOT NULL
);

-- 传承人及其允许展示的范围
CREATE TABLE IF NOT EXISTS inheritors (
    ref                  TEXT PRIMARY KEY,
    heritage_ref         TEXT NOT NULL REFERENCES heritage_projects(ref),
    public_name          TEXT NOT NULL,    -- 允许公开的称呼（可为代号）
    allowed_scopes       TEXT NOT NULL,    -- JSON 数组：允许展示的范围
    full_process_allowed INTEGER NOT NULL DEFAULT 0,  -- 完整工序是否可公开
    portrait_allowed     INTEGER NOT NULL DEFAULT 1,  -- 传承人肖像是否可公开
    notes                TEXT,
    created_at           TEXT NOT NULL
);

-- 体验场次：活动发生过的事实，任何授权变动都不删除
CREATE TABLE IF NOT EXISTS sessions (
    ref          TEXT PRIMARY KEY,
    heritage_ref TEXT NOT NULL REFERENCES heritage_projects(ref),
    inheritor_ref TEXT NOT NULL REFERENCES inheritors(ref),
    title        TEXT NOT NULL,
    venue        TEXT,
    occurred_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- 工序：关键工序只公开摘要，restricted_detail 仅馆员可见
CREATE TABLE IF NOT EXISTS process_steps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    heritage_ref      TEXT NOT NULL REFERENCES heritage_projects(ref),
    step_no           INTEGER NOT NULL,
    name              TEXT NOT NULL,
    public_summary    TEXT NOT NULL,
    restricted_detail TEXT,
    UNIQUE(heritage_ref, step_no)
);

-- 体验者：肖像同意独立于媒体许可，可撤回
CREATE TABLE IF NOT EXISTS attendees (
    ref              TEXT PRIMARY KEY,
    session_ref      TEXT NOT NULL REFERENCES sessions(ref),
    public_label     TEXT NOT NULL,
    portrait_consent TEXT NOT NULL DEFAULT 'denied'
                     CHECK (portrait_consent IN ('granted', 'denied', 'withdrawn')),
    consent_scopes   TEXT,                -- JSON 数组
    consented_at     TEXT,
    withdrawn_at     TEXT,
    created_at       TEXT NOT NULL
);

-- 材料包及其使用边界
CREATE TABLE IF NOT EXISTS material_kits (
    ref           TEXT PRIMARY KEY,
    heritage_ref  TEXT NOT NULL REFERENCES heritage_projects(ref),
    name          TEXT NOT NULL,
    public_note   TEXT,
    allowed_scopes TEXT NOT NULL,        -- JSON 数组：允许的使用边界
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_material_kits (
    session_ref TEXT NOT NULL REFERENCES sessions(ref),
    kit_ref     TEXT NOT NULL REFERENCES material_kits(ref),
    note        TEXT,
    PRIMARY KEY (session_ref, kit_ref)
);

-- 青年作品：青年作者保留独立授权范围
CREATE TABLE IF NOT EXISTS youth_works (
    ref            TEXT PRIMARY KEY,
    session_ref    TEXT NOT NULL REFERENCES sessions(ref),
    youth_ref      TEXT NOT NULL,       -- 匿名引用编号
    title          TEXT NOT NULL,
    allowed_scopes TEXT NOT NULL,       -- JSON 数组：青年允许的用途
    created_at     TEXT NOT NULL
);

-- 口译修订：同一 unit_ref 的每次修订都同时包含原文与译文，改动保持对应
CREATE TABLE IF NOT EXISTS translation_units (
    unit_ref        TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    heritage_ref    TEXT REFERENCES heritage_projects(ref),
    session_ref     TEXT REFERENCES sessions(ref),
    language_pair   TEXT NOT NULL,      -- 例如 zh/en
    source_text     TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    change_note     TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (unit_ref, revision)
);

-- 媒体素材：以 sha256 为唯一身份，重复影像识别为同一素材
CREATE TABLE IF NOT EXISTS media_assets (
    asset_ref            TEXT PRIMARY KEY,
    sha256               TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL
                         CHECK (kind IN ('video', 'audio', 'image', 'text', 'document')),
    title                TEXT NOT NULL,
    session_ref          TEXT REFERENCES sessions(ref),
    heritage_ref         TEXT REFERENCES heritage_projects(ref),
    inheritor_ref        TEXT REFERENCES inheritors(ref),
    contains_full_process INTEGER NOT NULL DEFAULT 0,
    technical_fingerprint TEXT,          -- JSON：分辨率、时长等非身份元数据
    first_seen_at        TEXT NOT NULL,
    created_at           TEXT NOT NULL
);

-- 上传记录：两家媒体分别上传同一段视频，各自留痕，但归并到同一素材
CREATE TABLE IF NOT EXISTS media_uploads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_ref     TEXT NOT NULL REFERENCES media_assets(asset_ref),
    outlet_ref    TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL,
    claimed_basis TEXT NOT NULL,         -- licensed | on-site-assumption 等
    note          TEXT
);

-- 素材中出现的体验者（肖像边界）
CREATE TABLE IF NOT EXISTS asset_attendees (
    asset_ref    TEXT NOT NULL REFERENCES media_assets(asset_ref),
    attendee_ref TEXT NOT NULL REFERENCES attendees(ref),
    PRIMARY KEY (asset_ref, attendee_ref)
);

-- 素材实际使用的青年作品
CREATE TABLE IF NOT EXISTS asset_youth_works (
    asset_ref TEXT NOT NULL REFERENCES media_assets(asset_ref),
    work_ref  TEXT NOT NULL REFERENCES youth_works(ref),
    PRIMARY KEY (asset_ref, work_ref)
);

-- 素材实际使用的材料包
CREATE TABLE IF NOT EXISTS asset_material_kits (
    asset_ref TEXT NOT NULL REFERENCES media_assets(asset_ref),
    kit_ref   TEXT NOT NULL REFERENCES material_kits(ref),
    PRIMARY KEY (asset_ref, kit_ref)
);

-- 素材配套的口译单元（取其最新修订）
CREATE TABLE IF NOT EXISTS asset_translation_units (
    asset_ref TEXT NOT NULL REFERENCES media_assets(asset_ref),
    unit_ref  TEXT NOT NULL,
    PRIMARY KEY (asset_ref, unit_ref)
);

-- 传播许可：挂在素材身份上，按媒体用途授权；可撤回，到期由时间推导
CREATE TABLE IF NOT EXISTS licenses (
    license_ref        TEXT PRIMARY KEY,
    asset_ref          TEXT NOT NULL REFERENCES media_assets(asset_ref),
    grantee_ref        TEXT NOT NULL,    -- 被授权媒体
    usage_scopes       TEXT NOT NULL,    -- JSON 数组：授予的媒体用途
    valid_from         TEXT NOT NULL,
    valid_until        TEXT,             -- NULL 表示长期
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'withdrawn')),
    covers_full_process INTEGER NOT NULL DEFAULT 0,
    granted_at         TEXT NOT NULL,
    withdrawn_at       TEXT,
    withdraw_reason    TEXT,
    created_at         TEXT NOT NULL
);

-- 媒体用途：报道、展项、短视频等
CREATE TABLE IF NOT EXISTS media_usages (
    usage_ref          TEXT PRIMARY KEY,
    asset_ref          TEXT NOT NULL REFERENCES media_assets(asset_ref),
    outlet_ref         TEXT NOT NULL,
    usage_type         TEXT NOT NULL,    -- report | exhibition | clip | documentary
    title              TEXT NOT NULL,
    channel            TEXT,
    status             TEXT NOT NULL DEFAULT 'published'
                       CHECK (status IN ('published', 'taken_down')),
    first_published_at TEXT NOT NULL,
    taken_down_at      TEXT,
    created_at         TEXT NOT NULL
);

-- 分发闸门判定流水：可追查每一次允许、拦截与下线
CREATE TABLE IF NOT EXISTS distribution_decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usage_ref  TEXT NOT NULL REFERENCES media_usages(usage_ref),
    asset_ref  TEXT NOT NULL REFERENCES media_assets(asset_ref),
    decision   TEXT NOT NULL
               CHECK (decision IN ('allowed', 'blocked', 'taken-down')),
    reasons    TEXT NOT NULL,            -- JSON 数组
    decided_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_licenses_asset ON licenses(asset_ref);
CREATE INDEX IF NOT EXISTS idx_usages_asset ON media_usages(asset_ref);
CREATE INDEX IF NOT EXISTS idx_uploads_asset ON media_uploads(asset_ref);
CREATE INDEX IF NOT EXISTS idx_decisions_usage ON distribution_decisions(usage_ref);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_domain');
