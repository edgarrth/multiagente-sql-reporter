CREATE TABLE IF NOT EXISTS analytics.dim_date (
  date_key date PRIMARY KEY,
  year_number integer NOT NULL,
  quarter_number integer NOT NULL,
  month_number integer NOT NULL,
  month_start date NOT NULL,
  month_name varchar(20) NOT NULL,
  week_number integer NOT NULL,
  day_of_week integer NOT NULL,
  is_weekend boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.dim_merchant (
  merchant_id integer PRIMARY KEY,
  merchant_name varchar(150) NOT NULL,
  mcc varchar(4) NOT NULL,
  city varchar(80) NOT NULL,
  country_code char(2) NOT NULL,
  segment varchar(20) NOT NULL,
  risk_level varchar(10) NOT NULL,
  onboarding_date date NOT NULL,
  active boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.fact_payment_transactions (
  transaction_id bigint PRIMARY KEY,
  merchant_id integer NOT NULL,
  transaction_date date NOT NULL,
  transaction_ts timestamptz NOT NULL,
  amount_pen numeric(14,2) NOT NULL,
  currency_code char(3) NOT NULL,
  channel varchar(20) NOT NULL,
  card_scheme varchar(20) NOT NULL,
  entry_mode varchar(20) NOT NULL,
  status varchar(20) NOT NULL,
  response_code varchar(4) NOT NULL,
  installment_count smallint NOT NULL,
  is_international boolean NOT NULL,
  settlement_status varchar(20) NOT NULL,
  fee_amount_pen numeric(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.fact_chargebacks (
  chargeback_id bigint PRIMARY KEY,
  transaction_id bigint NOT NULL,
  merchant_id integer NOT NULL,
  opened_date date NOT NULL,
  chargeback_amount_pen numeric(14,2) NOT NULL,
  reason_code varchar(20) NOT NULL,
  status varchar(20) NOT NULL,
  resolved_date date
);

TRUNCATE analytics.dim_date, analytics.dim_merchant,
  analytics.fact_payment_transactions, analytics.fact_chargebacks;

INSERT INTO analytics.dim_date
SELECT d::date,
       extract(year FROM d)::int,
       extract(quarter FROM d)::int,
       extract(month FROM d)::int,
       date_trunc('month', d)::date,
       to_char(d, 'TMMonth'),
       extract(week FROM d)::int,
       extract(isodow FROM d)::int,
       extract(isodow FROM d)::int IN (6, 7)
FROM generate_series(
  CURRENT_DATE - INTERVAL '400 days',
  CURRENT_DATE + INTERVAL '31 days',
  INTERVAL '1 day'
) AS d;

INSERT INTO analytics.dim_merchant
SELECT merchant_id, merchant_name, mcc, city, country_code, segment, risk_level,
       onboarding_date, active
FROM operational.merchants;

INSERT INTO analytics.fact_payment_transactions
SELECT transaction_id,
       merchant_id,
       (transaction_ts AT TIME ZONE :'business_timezone')::date,
       transaction_ts,
       amount_pen,
       currency_code,
       channel,
       card_scheme,
       entry_mode,
       status,
       response_code,
       installment_count,
       is_international,
       settlement_status,
       fee_amount_pen
FROM operational.payment_transactions
WHERE NOT is_test;

INSERT INTO analytics.fact_chargebacks
SELECT c.chargeback_id,
       c.transaction_id,
       t.merchant_id,
       c.opened_date,
       c.chargeback_amount_pen,
       c.reason_code,
       c.status,
       c.resolved_date
FROM operational.chargebacks c
JOIN operational.payment_transactions t USING (transaction_id)
WHERE NOT t.is_test;

CREATE INDEX IF NOT EXISTS idx_fact_payment_date
  ON analytics.fact_payment_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_payment_merchant_date
  ON analytics.fact_payment_transactions(merchant_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_payment_status_date
  ON analytics.fact_payment_transactions(status, transaction_date);
CREATE INDEX IF NOT EXISTS idx_fact_payment_transaction_ts
  ON analytics.fact_payment_transactions(transaction_ts DESC);
CREATE INDEX IF NOT EXISTS idx_fact_chargeback_opened_date
  ON analytics.fact_chargebacks(opened_date);

CREATE OR REPLACE VIEW semantic.v_payment_transactions AS
SELECT f.transaction_id,
       f.transaction_date,
       f.merchant_id,
       m.merchant_name,
       m.mcc,
       m.city,
       m.segment,
       m.risk_level,
       f.amount_pen,
       f.currency_code,
       f.channel,
       f.card_scheme,
       f.entry_mode,
       f.status,
       f.response_code,
       f.installment_count,
       f.is_international,
       f.settlement_status,
       f.fee_amount_pen,
       f.transaction_ts AS transaction_timestamp
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id);

CREATE OR REPLACE VIEW semantic.v_daily_payment_metrics AS
SELECT f.transaction_date AS metric_date,
       m.mcc,
       m.city,
       f.channel,
       f.card_scheme,
       count(*) AS transaction_count,
       count(*) FILTER (WHERE f.status = 'APPROVED') AS approved_count,
       count(*) FILTER (WHERE f.status = 'DECLINED') AS declined_count,
       count(*) FILTER (WHERE f.status = 'REVERSED') AS reversed_count,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END), 2)
         AS processed_amount_pen,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.fee_amount_pen ELSE 0 END), 2)
         AS fee_revenue_pen,
       round(
         count(*) FILTER (WHERE f.status = 'APPROVED')::numeric / NULLIF(count(*), 0),
         4
       ) AS approval_rate,
       round(
         sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END)
           / NULLIF(count(*) FILTER (WHERE f.status = 'APPROVED'), 0),
         2
       ) AS average_ticket_pen,
       round(
         count(*) FILTER (WHERE f.settlement_status = 'FAILED')::numeric
           / NULLIF(count(*) FILTER (WHERE f.status = 'APPROVED'), 0),
         4
       ) AS settlement_failure_rate
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id)
GROUP BY f.transaction_date, m.mcc, m.city, f.channel, f.card_scheme;

CREATE OR REPLACE VIEW semantic.v_merchant_performance AS
SELECT f.transaction_date AS metric_date,
       m.merchant_id,
       m.merchant_name,
       m.mcc,
       m.city,
       m.segment,
       m.risk_level,
       count(*) AS transaction_count,
       count(*) FILTER (WHERE f.status = 'APPROVED') AS approved_count,
       count(*) FILTER (WHERE f.status = 'DECLINED') AS declined_count,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END), 2)
         AS processed_amount_pen,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.fee_amount_pen ELSE 0 END), 2)
         AS fee_revenue_pen,
       round(
         count(*) FILTER (WHERE f.status = 'APPROVED')::numeric / NULLIF(count(*), 0),
         4
       ) AS approval_rate,
       round(
         sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END)
           / NULLIF(count(*) FILTER (WHERE f.status = 'APPROVED'), 0),
         2
       ) AS average_ticket_pen
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id)
GROUP BY f.transaction_date, m.merchant_id, m.merchant_name, m.mcc, m.city,
         m.segment, m.risk_level;

CREATE OR REPLACE VIEW semantic.v_merchant_settlement_metrics AS
SELECT f.transaction_date AS metric_date,
       m.merchant_id,
       m.merchant_name,
       m.mcc,
       m.city,
       m.segment,
       count(*) FILTER (WHERE f.status = 'APPROVED') AS approved_count,
       count(*) FILTER (WHERE f.settlement_status = 'SETTLED') AS settled_count,
       count(*) FILTER (WHERE f.settlement_status = 'PENDING') AS pending_settlement_count,
       count(*) FILTER (WHERE f.settlement_status = 'FAILED') AS failed_settlement_count,
       round(
         count(*) FILTER (WHERE f.settlement_status = 'FAILED')::numeric
           / NULLIF(count(*) FILTER (WHERE f.status = 'APPROVED'), 0),
         4
       ) AS settlement_failure_rate
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id)
GROUP BY f.transaction_date, m.merchant_id, m.merchant_name, m.mcc, m.city,
         m.segment;

CREATE OR REPLACE VIEW semantic.v_monthly_payment_metrics AS
SELECT date_trunc('month', f.transaction_date)::date AS metric_month,
       m.mcc,
       f.channel,
       f.card_scheme,
       count(*) AS transaction_count,
       count(*) FILTER (WHERE f.status = 'APPROVED') AS approved_count,
       count(*) FILTER (WHERE f.status = 'DECLINED') AS declined_count,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END), 2)
         AS processed_amount_pen,
       round(sum(CASE WHEN f.status = 'APPROVED' THEN f.fee_amount_pen ELSE 0 END), 2)
         AS fee_revenue_pen,
       round(
         count(*) FILTER (WHERE f.status = 'APPROVED')::numeric / NULLIF(count(*), 0),
         4
       ) AS approval_rate,
       round(
         sum(CASE WHEN f.status = 'APPROVED' THEN f.amount_pen ELSE 0 END)
           / NULLIF(count(*) FILTER (WHERE f.status = 'APPROVED'), 0),
         2
       ) AS average_ticket_pen
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id)
GROUP BY date_trunc('month', f.transaction_date)::date, m.mcc, f.channel, f.card_scheme;

CREATE OR REPLACE VIEW semantic.v_decline_analysis AS
SELECT f.transaction_date AS metric_date,
       m.mcc,
       m.city,
       f.channel,
       f.card_scheme,
       f.response_code,
       count(*) AS declined_count,
       round(sum(f.amount_pen), 2) AS declined_amount_pen
FROM analytics.fact_payment_transactions f
JOIN analytics.dim_merchant m USING (merchant_id)
WHERE f.status = 'DECLINED'
GROUP BY f.transaction_date, m.mcc, m.city, f.channel, f.card_scheme, f.response_code;

CREATE OR REPLACE VIEW semantic.v_chargeback_metrics AS
SELECT date_trunc('month', c.opened_date)::date AS metric_month,
       m.mcc,
       m.city,
       m.segment,
       c.reason_code,
       c.status AS chargeback_status,
       count(*) AS chargeback_count,
       round(sum(c.chargeback_amount_pen), 2) AS chargeback_amount_pen
FROM analytics.fact_chargebacks c
JOIN analytics.dim_merchant m USING (merchant_id)
GROUP BY date_trunc('month', c.opened_date)::date, m.mcc, m.city, m.segment,
         c.reason_code, c.status;

REVOKE ALL ON ALL TABLES IN SCHEMA operational, analytics FROM :"reader_role";
REVOKE CREATE ON SCHEMA semantic FROM :"reader_role";
GRANT USAGE ON SCHEMA semantic TO :"reader_role";
GRANT SELECT ON ALL TABLES IN SCHEMA semantic TO :"reader_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA semantic GRANT SELECT ON TABLES TO :"reader_role";
