UPDATE generations SET state = 'retired', retired_at = now() WHERE state <> 'retired';

DELETE FROM definitions;

ALTER TABLE definitions
    ADD COLUMN name_digest bytea NOT NULL,
    ADD COLUMN parent bigint,
    ADD COLUMN children bigint[] NOT NULL DEFAULT '{}';
