-- Distinguish real rooms from the other IfcSpace objects of the export
-- (facade zones, aggregate occupancy zones, voids). Only ROOM / CIRCULATION / ROOF
-- spaces take part in adjacency.
ALTER TABLE rooms ADD COLUMN space_kind TEXT NOT NULL DEFAULT 'ROOM';
CREATE INDEX IF NOT EXISTS ix_rooms_kind ON rooms(space_kind);
