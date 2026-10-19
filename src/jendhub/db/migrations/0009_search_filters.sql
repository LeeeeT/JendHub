ALTER TABLE searches ADD COLUMN packages text[] NOT NULL DEFAULT '{}',
    ADD COLUMN uses text[] NOT NULL DEFAULT '{}',
    ADD COLUMN used_by text[] NOT NULL DEFAULT '{}';
