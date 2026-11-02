CREATE TABLE releases (
    id text GENERATED ALWAYS AS (name || '@' || version) STORED UNIQUE,
    name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9-]{0,63}$'),
    version text NOT NULL CHECK (version ~ '^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){3}$'),
    version_order numeric[] GENERATED ALWAYS AS (string_to_array(version, '.')::numeric[]) STORED,
    hash text NOT NULL REFERENCES packages(hash),
    PRIMARY KEY (name, version)
);
CREATE INDEX releases_hash ON releases(hash);
INSERT INTO releases (name, version, hash)
SELECT name, version, hash FROM packages WHERE name IS NOT NULL;

UPDATE definitions d SET release = r.id FROM releases r WHERE d.release = r.hash;
UPDATE highlights h SET release = r.id FROM releases r WHERE h.release = r.hash;
UPDATE index_jobs j SET release = r.id FROM releases r WHERE j.release = r.hash;
ALTER TABLE invalid_releases DROP CONSTRAINT invalid_releases_hash_fkey;
UPDATE invalid_releases i SET hash = r.id FROM releases r WHERE i.hash = r.hash;
ALTER TABLE invalid_releases RENAME COLUMN hash TO release;
ALTER TABLE invalid_releases ADD FOREIGN KEY (release) REFERENCES releases(id);

ALTER TABLE packages DROP COLUMN version_order;
ALTER TABLE packages DROP COLUMN name;
ALTER TABLE packages DROP COLUMN version;

CREATE TABLE library_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0
);
INSERT INTO library_state DEFAULT VALUES;
ALTER TABLE invalid_releases ADD COLUMN library_revision bigint NOT NULL DEFAULT -1;
ALTER TABLE invalid_releases ADD COLUMN indexer_version integer NOT NULL DEFAULT -1;
