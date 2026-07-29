-- Deterministic synthetic portfolio: 250 merchants, 250,000 transactions over 365 days,
-- plus chargebacks derived from approved transactions. No personal or cardholder data is used.
INSERT INTO operational.merchants (
  merchant_id,
  merchant_name,
  legal_name,
  mcc,
  city,
  country_code,
  segment,
  risk_level,
  onboarding_date,
  active
)
SELECT merchant_id,
       'Comercio ' || lpad(merchant_id::text, 3, '0'),
       'Empresa Comercial ' || lpad(merchant_id::text, 3, '0') || ' S.A.C.',
       (ARRAY['5411','5812','5732','5311','5541','5912','4111','4511','4814','5651','5941','7011'])
         [1 + ((merchant_id - 1) % 12)],
       (ARRAY['Lima','Arequipa','Trujillo','Cusco','Piura','Chiclayo','Ica','Tacna','Huancayo','Cajamarca'])
         [1 + ((merchant_id - 1) % 10)],
       'PE',
       CASE
         WHEN merchant_id % 10 = 0 THEN 'LARGE'
         WHEN merchant_id % 3 = 0 THEN 'MEDIUM'
         ELSE 'SMALL'
       END,
       CASE
         WHEN merchant_id % 37 = 0 THEN 'HIGH'
         WHEN merchant_id % 7 = 0 THEN 'MEDIUM'
         ELSE 'LOW'
       END,
       CURRENT_DATE - ((merchant_id * 11) % 1200),
       merchant_id % 53 <> 0
FROM generate_series(1, 250) AS merchant_id
ON CONFLICT (merchant_id) DO NOTHING;

INSERT INTO operational.payment_transactions (
  transaction_id,
  merchant_id,
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
  fee_amount_pen,
  is_test,
  authorization_code
)
SELECT i,
       1 + ((i * 17) % 250),
       CURRENT_DATE - INTERVAL '365 days'
         + ((i * 97) % (365 * 24 * 60)) * INTERVAL '1 minute',
       round((8 + ((i * 31) % 2492) + ((i % 100)::numeric / 100))::numeric, 2),
       CASE WHEN i % 97 = 0 THEN 'USD' ELSE 'PEN' END,
       (ARRAY['POS','ECOMMERCE','CONTACTLESS','QR'])[1 + (i % 4)],
       (ARRAY['DINERS','VISA','MASTERCARD','AMEX'])[1 + ((i * 3) % 4)],
       CASE (i % 5)
         WHEN 0 THEN 'CHIP'
         WHEN 1 THEN 'CONTACTLESS'
         WHEN 2 THEN 'ECOMMERCE'
         WHEN 3 THEN 'MAGSTRIPE'
         ELSE 'QR'
       END,
       CASE
         WHEN i % 79 = 0 THEN 'REVERSED'
         WHEN i % 17 = 0 OR i % 41 BETWEEN 0 AND 2 THEN 'DECLINED'
         ELSE 'APPROVED'
       END,
       CASE
         WHEN i % 79 = 0 THEN 'R1'
         WHEN i % 41 BETWEEN 0 AND 2 THEN '51'
         WHEN i % 17 = 0 THEN (ARRAY['05','54','91'])[1 + (i % 3)]
         ELSE '00'
       END,
       CASE WHEN i % 13 = 0 THEN 6 WHEN i % 7 = 0 THEN 3 ELSE 1 END,
       i % 29 = 0,
       CASE
         WHEN i % 79 = 0 OR i % 17 = 0 OR i % 41 BETWEEN 0 AND 2 THEN 'NOT_APPLICABLE'
         WHEN i % 251 = 0 THEN 'FAILED'
         WHEN i % 5 = 0 THEN 'PENDING'
         ELSE 'SETTLED'
       END,
       round(
         (CASE WHEN i % 79 = 0 OR i % 17 = 0 OR i % 41 BETWEEN 0 AND 2
           THEN 0
           ELSE (8 + ((i * 31) % 2492)) * 0.021
         END)::numeric,
         2
       ),
       i % 997 = 0,
       CASE
         WHEN i % 79 = 0 OR i % 17 = 0 OR i % 41 BETWEEN 0 AND 2 THEN NULL
         ELSE lpad((10000000 + i)::text, 10, '0')
       END
FROM generate_series(1, 250000) AS i
ON CONFLICT (transaction_id) DO NOTHING;

INSERT INTO operational.chargebacks (
  chargeback_id,
  transaction_id,
  opened_date,
  chargeback_amount_pen,
  reason_code,
  status,
  resolved_date
)
WITH candidates AS (
  SELECT t.*,
         (t.transaction_ts AT TIME ZONE :'business_timezone')::date
           + (7 + (t.transaction_id % 35))::int AS calculated_opened_date
  FROM operational.payment_transactions t
  WHERE t.status = 'APPROVED'
    AND NOT t.is_test
    AND t.transaction_id % 211 = 0
    AND (t.transaction_ts AT TIME ZONE :'business_timezone')::date <= CURRENT_DATE - 45
)
SELECT row_number() OVER (ORDER BY transaction_id),
       transaction_id,
       calculated_opened_date,
       round((amount_pen * (0.5 + ((transaction_id % 50)::numeric / 100)))::numeric, 2),
       (ARRAY['FRAUD','SERVICE_NOT_PROVIDED','DUPLICATE','PROCESSING_ERROR'])
         [1 + (transaction_id % 4)],
       CASE
         WHEN calculated_opened_date >= CURRENT_DATE - 30 THEN 'OPEN'
         ELSE (ARRAY['WON','LOST','CLOSED'])[1 + (transaction_id % 3)]
       END,
       CASE
         WHEN calculated_opened_date >= CURRENT_DATE - 30 THEN NULL
         ELSE LEAST(calculated_opened_date + (20 + (transaction_id % 70))::int, CURRENT_DATE)
       END
FROM candidates
ON CONFLICT (chargeback_id) DO NOTHING;
