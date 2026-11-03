-- The production tables the service reads and writes, reduced to the columns it
-- uses. Tests only.
CREATE TABLE senate_citations (id INTEGER PRIMARY KEY, commission VARCHAR, commission_original VARCHAR,
    date DATE, time TIME, materia TEXT, boletin VARCHAR, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE camara_citations (LIKE senate_citations INCLUDING ALL);
CREATE TABLE senate_sessions (id INTEGER PRIMARY KEY, citation_id INTEGER, run_id VARCHAR, commission VARCHAR,
    date DATE, time TIME, title VARCHAR, materia TEXT, boletin TEXT, transcript_text TEXT, analysis_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE camara_sessions (LIKE senate_sessions INCLUDING ALL);
ALTER TABLE camara_sessions ADD COLUMN commission_original VARCHAR;
CREATE TABLE plenary_turns (id BIGSERIAL PRIMARY KEY, chamber VARCHAR(10) NOT NULL, plenary_id INTEGER NOT NULL,
    turn_index INTEGER NOT NULL, start_seconds DOUBLE PRECISION, end_seconds DOUBLE PRECISION, text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE boletines (boletin VARCHAR PRIMARY KEY, titulo TEXT);
CREATE TABLE bicameral_keywords (id INTEGER PRIMARY KEY, user_id INTEGER, keyword VARCHAR, type VARCHAR,
    match_type VARCHAR, email_enabled BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
CREATE TABLE user_tracked_boletines (user_id INTEGER, boletin VARCHAR, alert_on_mentions BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (user_id, boletin));
CREATE TABLE bicameral_notifications (id SERIAL PRIMARY KEY, user_id INTEGER, keyword_id INTEGER, type VARCHAR,
    related_id INTEGER, chamber VARCHAR, message TEXT, boletin VARCHAR, origin VARCHAR, target_type VARCHAR,
    target_id INTEGER, target_chamber VARCHAR, target_boletin VARCHAR, created_at TIMESTAMP DEFAULT NOW());
CREATE TABLE citation_keyword_notification_sent (user_id INTEGER, keyword_id INTEGER, citation_id INTEGER,
    citation_date DATE, PRIMARY KEY (user_id, keyword_id, citation_id));
CREATE TABLE citation_boletin_notification_sent (user_id INTEGER, citation_id INTEGER, boletin VARCHAR,
    citation_date DATE, PRIMARY KEY (user_id, citation_id, boletin));
