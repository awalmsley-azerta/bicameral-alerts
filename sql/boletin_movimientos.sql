-- Boletín movements, for the alerts service. Run once in the Bicameral database
-- (the content side) in the RDS Query Editor. Safe to run twice.
--
-- The boletines scraper writes one row whenever a boletín's trámite count grows
-- past a count it had already seen (boletin_tracking_state). The first count
-- seen for a boletín only sets the baseline and is not a movement. The alerts
-- service reads these rows by id and alerts each site's followers, so the
-- scraper needs no user data.

CREATE TABLE IF NOT EXISTS boletin_movimientos (
    id                BIGSERIAL    PRIMARY KEY,
    boletin           VARCHAR(30)  NOT NULL,
    tramites_antes    INTEGER      NOT NULL,
    tramites_despues  INTEGER      NOT NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS boletin_movimientos_boletin ON boletin_movimientos (boletin);
