-- =====================================================================
-- Important queries, DML, advanced-feature demos and test cases (MySQL 8)
-- Open a client with:  mysql -u root -p bank_fraud_db
--
-- The database starts EMPTY. Before running sections A-D:
--   1. Sign up two customers in the app (use two browsers or a private window).
--   2. Add funds to the first customer's account in the app.
--   3. Run the "pick two accounts" block below, then the rest section by section.
-- Screenshot each result for your documentation.
-- =====================================================================

SET time_zone = '+00:00';

-- ---- pick two accounts to use in the demos below ----
SET @acct_a = (SELECT MIN(account_id) FROM accounts);
SET @acct_b = (SELECT MAX(account_id) FROM accounts);
SELECT @acct_a AS acct_a, @acct_b AS acct_b;   -- they must be different accounts

-- ############ A. SELECT QUERIES (JOIN, GROUP BY, HAVING, subquery, window) ############

-- A1. JOIN: every transaction with sender and receiver names
SELECT t.txn_id, t.created_at, t.txn_type, t.amount,
       sc.full_name AS sender, rc.full_name AS receiver
  FROM transactions t
  LEFT JOIN accounts  fa ON fa.account_id  = t.from_account_id
  LEFT JOIN customers sc ON sc.customer_id = fa.customer_id
  LEFT JOIN accounts  ta ON ta.account_id  = t.to_account_id
  LEFT JOIN customers rc ON rc.customer_id = ta.customer_id
 ORDER BY t.created_at DESC;

-- A2. GROUP BY + HAVING + aggregates: customers who sent more than 10,000 in total
SELECT c.full_name, COUNT(t.txn_id) AS txn_count, SUM(t.amount) AS total_sent
  FROM customers c
  JOIN accounts a     ON a.customer_id = c.customer_id
  JOIN transactions t ON t.from_account_id = a.account_id
 GROUP BY c.customer_id, c.full_name
HAVING SUM(t.amount) > 10000
 ORDER BY total_sent DESC;

-- A3. Daily transaction volume
SELECT DATE(created_at) AS txn_date, COUNT(*) AS txn_count,
       SUM(amount) AS total_amount, ROUND(AVG(amount), 2) AS avg_amount
  FROM transactions
 GROUP BY DATE(created_at)
 ORDER BY txn_date DESC;

-- A4. Correlated subquery: a transaction more than 3x the sender's average of OTHER transactions
SELECT t.txn_id, t.from_account_id, t.amount
  FROM transactions t
 WHERE t.from_account_id IS NOT NULL
   AND t.amount > 3 * (SELECT AVG(t2.amount)
                         FROM transactions t2
                        WHERE t2.from_account_id = t.from_account_id
                          AND t2.txn_id <> t.txn_id);

-- A5. Customers with more than one account
SELECT c.full_name, COUNT(a.account_id) AS accounts, SUM(a.balance) AS total_balance
  FROM customers c JOIN accounts a ON a.customer_id = c.customer_id
 GROUP BY c.customer_id, c.full_name
HAVING COUNT(a.account_id) > 1;

-- A6. Open fraud flags, highest risk first (uses the view)
SELECT flag_id, customer_name, rule_name, amount, location_city, risk_score, flagged_at
  FROM v_suspicious_transactions
 WHERE flag_status = 'open'
 ORDER BY risk_score DESC, flagged_at DESC;

-- A7. Rule effectiveness: how many flags each rule raised and how analysts judged them
SELECT fr.rule_name,
       COUNT(f.flag_id)                         AS total_flags,
       COALESCE(SUM(f.status = 'confirmed'), 0) AS confirmed,
       COALESCE(SUM(f.status = 'dismissed'), 0) AS dismissed,
       COALESCE(SUM(f.status = 'open'), 0)      AS still_open
  FROM fraud_rules fr
  LEFT JOIN fraud_flags f ON f.rule_id = fr.rule_id
 GROUP BY fr.rule_id, fr.rule_name
 ORDER BY total_flags DESC;

-- A8. Window function: running total of money sent per account
SELECT from_account_id, txn_id, created_at, amount,
       SUM(amount) OVER (PARTITION BY from_account_id ORDER BY created_at, txn_id) AS running_total
  FROM transactions
 WHERE from_account_id IS NOT NULL
 ORDER BY from_account_id, created_at, txn_id;

-- A9. Customers with at least two fraud flags
SELECT customer_name, COUNT(*) AS flag_count, MAX(risk_score) AS max_risk
  FROM v_suspicious_transactions
 GROUP BY customer_name
HAVING COUNT(*) >= 2
 ORDER BY flag_count DESC, max_risk DESC;

-- A10. Account summary view
SELECT * FROM v_account_summary ORDER BY account_id;

-- A11. New sign-ups per day
SELECT DATE(created_at) AS day, COUNT(*) AS new_users
  FROM users WHERE role = 'customer'
 GROUP BY DATE(created_at) ORDER BY day DESC;

-- ############ B. DML: INSERT / UPDATE / DELETE ############

-- B1. UPDATE: change a fraud rule threshold (the trigger reads the table, so it takes effect at once)
UPDATE fraud_rules SET threshold_amount = 20000 WHERE rule_type = 'HIGH_AMOUNT';
-- ...and put it back:
UPDATE fraud_rules SET threshold_amount = 50000 WHERE rule_type = 'HIGH_AMOUNT';

-- B2. UPDATE: analyst confirms the highest-risk open flag and the sender's account is frozen
SET @flag = (SELECT flag_id FROM fraud_flags WHERE status = 'open' ORDER BY risk_score DESC, flag_id LIMIT 1);
UPDATE fraud_flags SET status = 'confirmed',
       reviewed_by = (SELECT MIN(user_id) FROM users WHERE role = 'admin'), reviewed_at = NOW()
 WHERE flag_id = @flag;
UPDATE accounts SET status = 'frozen'
 WHERE account_id = (SELECT from_account_id FROM (
         SELECT t.from_account_id FROM transactions t JOIN fraud_flags f ON f.txn_id = t.txn_id
          WHERE f.flag_id = @flag) AS x);

-- B3. DELETE: remove dismissed flags older than 90 days (affects 0 rows on a fresh system)
DELETE FROM fraud_flags WHERE status = 'dismissed' AND reviewed_at < NOW() - INTERVAL 90 DAY;

-- ############ C. ADVANCED FEATURE DEMOS ############

-- C1. TRANSACTIONS + PROCEDURES: add funds, then a normal transfer (no flag expected)
CALL deposit_funds(@acct_a, 100000, 'online', NULL, 'Demo funding');
CALL transfer_funds(@acct_a, @acct_b, 5000, 'online', NULL, 'Rent');
SELECT account_id, balance FROM accounts WHERE account_id IN (@acct_a, @acct_b);

-- C2. TRIGGER: a large transfer from a city that is not the sender's home city -> 2 new flags
--     (replace 'Paris' with any city that differs from the sender's home city)
CALL transfer_funds(@acct_a, @acct_b, 60000, 'online', 'Paris', 'Large transfer from a new city');
SELECT rule_name, risk_score, amount, location_city
  FROM v_suspicious_transactions ORDER BY flag_id DESC LIMIT 2;

-- C3. ROLLBACK: nothing is saved when a transaction is cancelled
START TRANSACTION;
UPDATE accounts SET balance = balance - 1000 WHERE account_id = @acct_a;
SELECT account_id, balance FROM accounts WHERE account_id = @acct_a;   -- shows the reduced balance
ROLLBACK;
SELECT account_id, balance FROM accounts WHERE account_id = @acct_a;   -- back to the original

-- C4. INDEXING: compare the plan with and without the index
EXPLAIN SELECT * FROM transactions
 WHERE from_account_id = @acct_a AND created_at >= NOW() - INTERVAL 1 DAY;   -- key: idx_txn_from_created
ALTER TABLE transactions DROP INDEX idx_txn_from_created;
EXPLAIN SELECT * FROM transactions
 WHERE from_account_id = @acct_a AND created_at >= NOW() - INTERVAL 1 DAY;   -- different (slower) plan
ALTER TABLE transactions ADD INDEX idx_txn_from_created (from_account_id, created_at);
-- Tip: EXPLAIN ANALYZE (MySQL 8.0.18+) also shows actual timings.

-- ############ D. TEST CASES (each statement below should FAIL as described) ############
-- Uncomment one at a time and screenshot the error for your Testing Results section.

-- D1. Negative balance blocked                  -> violates chk_balance
-- UPDATE accounts SET balance = -100 WHERE account_id = @acct_a;

-- D2. Zero / negative amount blocked            -> violates chk_txn_amount
-- INSERT INTO transactions (from_account_id, to_account_id, txn_type, amount) VALUES (@acct_a, @acct_b, 'transfer', 0);

-- D3. Transfer to the same account              -> 'Cannot transfer to the same account'
-- CALL transfer_funds(@acct_a, @acct_a, 100, 'online', NULL, NULL);

-- D4. Insufficient funds                        -> 'Insufficient funds' and everything rolls back
-- CALL transfer_funds(@acct_a, @acct_b, 99999999, 'online', NULL, NULL);

-- D5. Invalid role                              -> violates chk_user_role
-- INSERT INTO users (email, password_hash, role) VALUES ('x@y.com', 'h', 'hacker');

-- D6. Duplicate email                           -> violates unique key uq_users_email
-- INSERT INTO users (email, password_hash) SELECT email, 'h' FROM users LIMIT 1;

-- D7. Deleting a customer who has accounts      -> blocked by the foreign key on accounts.customer_id
-- DELETE FROM customers WHERE customer_id = (SELECT customer_id FROM accounts LIMIT 1);

-- D8. Deposit into an account that does not exist -> 'Account not found'
-- CALL deposit_funds(0, 100, 'online', NULL, NULL);

-- D9. Bad phone number                          -> violates chk_phone
-- INSERT INTO customers (user_id, full_name, phone, date_of_birth, home_city)
--   VALUES ((SELECT MIN(user_id) FROM users), 'T', '123', '1990-01-01', 'X');
