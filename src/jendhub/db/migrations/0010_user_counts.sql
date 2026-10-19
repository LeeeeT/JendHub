ALTER TABLE definitions RENAME COLUMN users TO release_users;

ALTER TABLE definitions DROP COLUMN external;

CREATE TABLE user_counts (
    generation bigint NOT NULL REFERENCES generations ON DELETE CASCADE,
    target_key text NOT NULL,
    users integer NOT NULL,
    PRIMARY KEY (generation, target_key)
);

CREATE INDEX user_counts_most_used ON user_counts (generation, users DESC, target_key);

INSERT INTO user_counts (generation, target_key, users)
SELECT generation, target_key, sum(users)::integer FROM (
    SELECT generation, key AS target_key, release_users AS users FROM definitions
    UNION ALL
    SELECT generation, target_key, count(*) FROM external_uses GROUP BY generation, target_key
) found
GROUP BY generation, target_key;
