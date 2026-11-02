ALTER TABLE invalid_releases ADD COLUMN missing_names text[] NOT NULL DEFAULT '{}';
ALTER TABLE invalid_releases ADD COLUMN missing_hashes text[] NOT NULL DEFAULT '{}';
UPDATE invalid_releases i SET missing_names = ARRAY(
    SELECT DISTINCT (m.captures)[1]
    FROM jsonb_each(i.errors) e
    CROSS JOIN LATERAL regexp_matches(
        e.value->>'expected', '^a package named ([^ ]+) on '
    ) m(captures)
), missing_hashes = ARRAY(
    SELECT DISTINCT (m.captures)[1]
    FROM jsonb_each(i.errors) e
    CROSS JOIN LATERAL regexp_matches(
        e.value->>'expected',
        '^a file at https://hub[.]bend-lang[.]com/(0x[0-9a-f]{32})/manifest hashing to '
    ) m(captures)
    WHERE NOT EXISTS (SELECT FROM packages p WHERE p.hash = (m.captures)[1])
);
