\set ON_ERROR_STOP on

BEGIN;

CREATE ROLE dataquery_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT CONNECT ON DATABASE dataquery TO dataquery_reader;
GRANT USAGE ON SCHEMA public TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_customers TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_geolocation TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_order_items TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_order_payments TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_order_reviews TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_orders TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_products TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_sellers TO dataquery_reader;
GRANT SELECT ON TABLE public.ods_product_category_name_translation TO dataquery_reader;

COMMIT;
