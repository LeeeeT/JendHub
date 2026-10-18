ALTER TABLE searches DROP CONSTRAINT searches_client_check,
    ADD CONSTRAINT searches_client_check CHECK (client IN ('agents', 'web', 'mcp')),
    ADD COLUMN client_name text;
