CREATE TABLE dim.date (
    date_value date NOT NULL,
    calendar_year integer NOT NULL,
    calendar_month integer NOT NULL,
    month_start date NOT NULL,
    iso_week_year integer NOT NULL,
    iso_week integer NOT NULL,
    iso_weekday integer NOT NULL,
    PRIMARY KEY (date_value)
);
