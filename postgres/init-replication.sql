-- Tạo replication user để S2 có thể kết nối streaming replication
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
    CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator_pass';
  END IF;
END
$$;
