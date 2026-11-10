\set ON_ERROR_STOP on

BEGIN;

CREATE ROLE dataquery_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE CONNECT, TEMPORARY ON DATABASE olist FROM PUBLIC;
GRANT CONNECT ON DATABASE olist TO dataquery_reader;
REVOKE CREATE, TEMPORARY ON DATABASE olist FROM dataquery_reader;
REVOKE ALL ON SCHEMA ods FROM PUBLIC;
GRANT USAGE ON SCHEMA ods TO dataquery_reader;
REVOKE CREATE ON SCHEMA ods FROM dataquery_reader;
GRANT SELECT ON TABLE ods.customers TO dataquery_reader;
GRANT SELECT ON TABLE ods.geolocation TO dataquery_reader;
GRANT SELECT ON TABLE ods.order_items TO dataquery_reader;
GRANT SELECT ON TABLE ods.order_payments TO dataquery_reader;
GRANT SELECT ON TABLE ods.order_reviews TO dataquery_reader;
GRANT SELECT ON TABLE ods.orders TO dataquery_reader;
GRANT SELECT ON TABLE ods.products TO dataquery_reader;
GRANT SELECT ON TABLE ods.sellers TO dataquery_reader;
GRANT SELECT ON TABLE ods.product_category_name_translation TO dataquery_reader;

COMMIT;
