CREATE TABLE searches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    searched_at timestamptz NOT NULL DEFAULT now(),
    client text CHECK (client IN ('agents', 'web')),
    user_agent text,
    query text NOT NULL,
    page_offset integer NOT NULL,
    page_limit integer NOT NULL,
    generation bigint NOT NULL,
    results text[] NOT NULL,
    seconds double precision NOT NULL
);

CREATE INDEX searches_searched_at ON searches (searched_at);
