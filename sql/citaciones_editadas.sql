-- Edited citations, for the alerts service. Run once in the Bicameral database
-- (the content side) in the RDS Query Editor. Safe to run twice.
--
-- The citation scrapers rewrite a citation in place when its materia, boletín,
-- source URL or long commission name changes, and re-check it against the
-- keywords then. The service sees new citations by id; this stamps the edits so
-- it can see those too. A trigger, so no writer has to remember it. Rows are
-- never stamped on INSERT, only when one of those four columns really changes.

ALTER TABLE senate_citations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE camara_citations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION citacion_editada() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.materia, NEW.boletin, NEW.source_url, NEW.commission_original)
       IS DISTINCT FROM (OLD.materia, OLD.boletin, OLD.source_url, OLD.commission_original) THEN
        NEW.updated_at := NOW();
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS citacion_editada ON senate_citations;
CREATE TRIGGER citacion_editada BEFORE UPDATE ON senate_citations
    FOR EACH ROW EXECUTE FUNCTION citacion_editada();

DROP TRIGGER IF EXISTS citacion_editada ON camara_citations;
CREATE TRIGGER citacion_editada BEFORE UPDATE ON camara_citations
    FOR EACH ROW EXECUTE FUNCTION citacion_editada();

CREATE INDEX IF NOT EXISTS senate_citations_updated_at ON senate_citations (updated_at) WHERE updated_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS camara_citations_updated_at ON camara_citations (updated_at) WHERE updated_at IS NOT NULL;
