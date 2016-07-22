CREATE TABLE IF NOT EXISTS question_tags (
    id SERIAL PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions ON DELETE CASCADE ON UPDATE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags ON DELETE CASCADE ON UPDATE CASCADE,
    number INTEGER,
    UNIQUE (question_id, tag_id)
);
