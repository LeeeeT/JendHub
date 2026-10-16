ALTER TABLE generations
    ADD COLUMN dimensions integer,
    ADD COLUMN quantization text;

UPDATE generations g
SET dimensions = c.dimensions, quantization = c.quantization
FROM collections c
WHERE c.generation = g.id;

DELETE FROM generations WHERE dimensions IS NULL;

ALTER TABLE generations
    ALTER COLUMN dimensions SET NOT NULL,
    ALTER COLUMN quantization SET NOT NULL,
    ADD CHECK (dimensions > 0),
    ADD CHECK (quantization IN ('none', 'int8', 'binary'));

DROP TABLE collections;
