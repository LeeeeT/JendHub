CREATE TABLE highlights (
    generation bigint NOT NULL REFERENCES generations ON DELETE CASCADE,
    release text NOT NULL,
    module text NOT NULL,
    lines jsonb NOT NULL,
    PRIMARY KEY (generation, release, module)
);
