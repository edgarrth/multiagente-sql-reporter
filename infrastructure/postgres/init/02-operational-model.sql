CREATE SCHEMA IF NOT EXISTS operational AUTHORIZATION app_owner;
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION app_owner;
CREATE SCHEMA IF NOT EXISTS semantic AUTHORIZATION app_owner;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE TABLE IF NOT EXISTS public.axiz_bootstrap_metadata (
  key text PRIMARY KEY,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON SCHEMA operational, analytics FROM agent_reader;
GRANT USAGE ON SCHEMA semantic TO agent_reader;

CREATE TABLE IF NOT EXISTS operational.merchants (
  merchant_id integer PRIMARY KEY,
  merchant_name varchar(150) NOT NULL,
  legal_name varchar(180) NOT NULL,
  mcc varchar(4) NOT NULL,
  city varchar(80) NOT NULL,
  country_code char(2) NOT NULL DEFAULT 'PE',
  segment varchar(20) NOT NULL CHECK (segment IN ('SMALL','MEDIUM','LARGE')),
  risk_level varchar(10) NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
  onboarding_date date NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operational.payment_transactions (
  transaction_id bigint PRIMARY KEY,
  merchant_id integer NOT NULL REFERENCES operational.merchants(merchant_id),
  transaction_ts timestamptz NOT NULL,
  amount_pen numeric(14,2) NOT NULL CHECK (amount_pen >= 0),
  currency_code char(3) NOT NULL DEFAULT 'PEN',
  channel varchar(20) NOT NULL CHECK (channel IN ('POS','ECOMMERCE','CONTACTLESS','QR')),
  card_scheme varchar(20) NOT NULL CHECK (card_scheme IN ('DINERS','VISA','MASTERCARD','AMEX')),
  entry_mode varchar(20) NOT NULL CHECK (
    entry_mode IN ('CHIP','CONTACTLESS','ECOMMERCE','MAGSTRIPE','QR')
  ),
  status varchar(20) NOT NULL CHECK (status IN ('APPROVED','DECLINED','REVERSED')),
  response_code varchar(4) NOT NULL,
  installment_count smallint NOT NULL DEFAULT 1 CHECK (installment_count BETWEEN 1 AND 36),
  is_international boolean NOT NULL DEFAULT false,
  settlement_status varchar(20) NOT NULL CHECK (
    settlement_status IN ('PENDING','SETTLED','FAILED','NOT_APPLICABLE')
  ),
  fee_amount_pen numeric(12,2) NOT NULL DEFAULT 0 CHECK (fee_amount_pen >= 0),
  is_test boolean NOT NULL DEFAULT false,
  authorization_code varchar(12),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operational.chargebacks (
  chargeback_id bigint PRIMARY KEY,
  transaction_id bigint NOT NULL REFERENCES operational.payment_transactions(transaction_id),
  opened_date date NOT NULL,
  chargeback_amount_pen numeric(14,2) NOT NULL CHECK (chargeback_amount_pen > 0),
  reason_code varchar(20) NOT NULL,
  status varchar(20) NOT NULL CHECK (status IN ('OPEN','WON','LOST','CLOSED')),
  resolved_date date,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payment_tx_date
  ON operational.payment_transactions(transaction_ts);
CREATE INDEX IF NOT EXISTS idx_payment_tx_merchant_date
  ON operational.payment_transactions(merchant_id, transaction_ts);
CREATE INDEX IF NOT EXISTS idx_payment_tx_status_date
  ON operational.payment_transactions(status, transaction_ts);
CREATE INDEX IF NOT EXISTS idx_chargebacks_opened_date
  ON operational.chargebacks(opened_date);
