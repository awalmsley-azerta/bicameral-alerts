-- The alerts service's own state, in each site's database. Run once per site in
-- the RDS Query Editor before the service writes (the backtest and the --simular
-- dry run need none of this). Safe to run twice.

-- One row per alert ever sent: (user_id, regla, objetivo) is what makes a repeat,
-- a restart or a re-run a no-op, even after the user deletes the notification.
--   regla    keyword:<id> | mencion:<boletin> | movimiento:<boletin>:<tramites>
--   objetivo session:<chamber>:<id> | citation:<chamber>:<id> | boletin:<boletin>
--            | plenary:<chamber>:<id>:<window>
CREATE TABLE IF NOT EXISTS alertas_enviadas (
    user_id          INTEGER      NOT NULL,
    regla            VARCHAR(200) NOT NULL,
    objetivo         VARCHAR(200) NOT NULL,
    notification_id  INTEGER,
    creado_en        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, regla, objetivo)
);

-- One watermark per source: the last content id the service has matched.
CREATE TABLE IF NOT EXISTS alertas_estado (
    fuente          VARCHAR(50) PRIMARY KEY,
    marca           BIGINT      NOT NULL,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
