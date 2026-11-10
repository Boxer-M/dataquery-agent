#!/bin/sh

set -eu

raw_dir=${1:-data/raw}
container_dir=/tmp/dataquery_agent_olist
files='olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_orders_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv'

for file_name in $files; do
    if test ! -f "$raw_dir/$file_name"; then
        echo "Missing raw file: $raw_dir/$file_name" >&2
        exit 1
    fi
done

docker compose exec -T db mkdir -p "$container_dir"

for file_name in $files; do
    docker compose cp "$raw_dir/$file_name" "db:$container_dir/$file_name" >/dev/null
done

docker compose exec -T db sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d olist -w -f -' \
    < sql/load_olist_ods.sql
