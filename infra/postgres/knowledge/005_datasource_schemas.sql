-- Which schemas a datasource exposes.
--
-- DB_ALLOWED_SCHEMAS is a single global setting, which was sufficient while
-- there was one analytics database. A second datasource has its own schema
-- names, so the scope has to travel with the datasource rather than with the
-- process: scanning or querying one database must not be governed by another's
-- configuration.
--
-- Defaults to the historical value so existing rows keep behaving exactly as
-- they did.
ALTER TABLE knowledge.data_sources
    ADD COLUMN allowed_schemas text[] NOT NULL DEFAULT ARRAY['analytics'];
