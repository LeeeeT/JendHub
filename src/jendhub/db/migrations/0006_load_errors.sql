DELETE FROM index_jobs WHERE state = 'invalid';
DELETE FROM invalid_releases;
ALTER TABLE invalid_releases DROP COLUMN reason, ADD COLUMN errors jsonb NOT NULL;
