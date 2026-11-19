-- =====================================================================
-- Bank Transaction & Fraud Detection System  |  MySQL 8.0.16+ schema
-- Run from the project folder:
--     mysql -u root -p -e "source db/schema.sql"
-- WARNING: it drops and recreates the whole database, so re-running it erases all data.
-- It creates the structure plus reference data only: branches and the 3 fraud rules.
-- There are no users, accounts or transactions; customers sign up in the app, and the
-- analyst account is created with:  python scripts/create_admin.py
-- =====================================================================

DROP DATABASE IF EXISTS bank_fraud_db;
CREATE DATABASE bank_fraud_db CHARACTER SET utf8mb4;
USE bank_fraud_db;

-- Store and compare every timestamp in UTC
SET time_zone = '+00:00';

-- ---------------------------------------------------------------------
-- 1. TABLES
-- ---------------------------------------------------------------------

-- Login credentials + role (RBAC: customer / admin)
CREATE TABLE users (
    user_id        INT          NOT NULL AUTO_INCREMENT,
    email          VARCHAR(150) NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,              -- salted hash, never plain text
    role           VARCHAR(20)  NOT NULL DEFAULT 'customer',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email),
    CONSTRAINT chk_user_role  CHECK (role IN ('customer', 'admin')),
    CONSTRAINT chk_user_email CHECK (email LIKE '%_@_%._%')
) ENGINE = InnoDB;

-- Profile of a bank customer (1:1 with a customer-role user)
CREATE TABLE customers (
    customer_id    INT          NOT NULL AUTO_INCREMENT,
    user_id        INT          NOT NULL,
    full_name      VARCHAR(100) NOT NULL,
    phone          VARCHAR(15)  NOT NULL,
    date_of_birth  DATE         NOT NULL,
    home_city      VARCHAR(60)  NOT NULL,              -- used by the unusual-location rule
    kyc_status     VARCHAR(15)  NOT NULL DEFAULT 'pending',
    PRIMARY KEY (customer_id),
    UNIQUE KEY uq_customers_user  (user_id),
    UNIQUE KEY uq_customers_phone (phone),
    CONSTRAINT fk_customers_user FOREIGN KEY (user_id)
        REFERENCES users (user_id) ON DELETE CASCADE,
    CONSTRAINT chk_kyc   CHECK (kyc_status IN ('pending', 'verified', 'rejected')),
    CONSTRAINT chk_phone CHECK (phone REGEXP '^[0-9]{10,15}$')
) ENGINE = InnoDB;

CREATE TABLE branches (
    branch_id      INT          NOT NULL AUTO_INCREMENT,
    branch_name    VARCHAR(100) NOT NULL,
    ifsc_code      VARCHAR(11)  NOT NULL,
    city           VARCHAR(60)  NOT NULL,
    PRIMARY KEY (branch_id),
    UNIQUE KEY uq_branches_ifsc (ifsc_code)
) ENGINE = InnoDB;

CREATE TABLE accounts (
    account_id     INT           NOT NULL AUTO_INCREMENT,
    customer_id    INT           NOT NULL,
    branch_id      INT           NOT NULL,
    account_number VARCHAR(16)   NOT NULL,
    account_type   VARCHAR(10)   NOT NULL DEFAULT 'savings',
    balance        DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    status         VARCHAR(10)   NOT NULL DEFAULT 'active',
    opened_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id),
    UNIQUE KEY uq_accounts_number (account_number),
    KEY idx_accounts_customer (customer_id),
    KEY idx_accounts_branch (branch_id),
    CONSTRAINT fk_accounts_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_accounts_branch   FOREIGN KEY (branch_id)   REFERENCES branches (branch_id),
    CONSTRAINT chk_acc_type   CHECK (account_type IN ('savings', 'current')),
    CONSTRAINT chk_acc_status CHECK (status IN ('active', 'frozen', 'closed')),
    CONSTRAINT chk_balance    CHECK (balance >= 0)       -- no negative balance, enforced by the DB
) ENGINE = InnoDB;

-- One row per money movement.
--   deposit    : to_account only
--   withdrawal : from_account only
--   transfer   : both accounts (and they must differ)
-- (The two account foreign keys deliberately have no ON DELETE/UPDATE action,
--  because MySQL does not allow CHECK constraints on columns with such actions.)
CREATE TABLE transactions (
    txn_id           BIGINT        NOT NULL AUTO_INCREMENT,
    from_account_id  INT           NULL,
    to_account_id    INT           NULL,
    txn_type         VARCHAR(12)   NOT NULL,
    amount           DECIMAL(15,2) NOT NULL,
    channel          VARCHAR(10)   NOT NULL DEFAULT 'online',
    location_city    VARCHAR(60)   NULL,
    description      TEXT          NULL,
    status           VARCHAR(10)   NOT NULL DEFAULT 'completed',
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (txn_id),
    KEY idx_txn_from_created (from_account_id, created_at),
    KEY idx_txn_to_created   (to_account_id, created_at),
    KEY idx_txn_created      (created_at),
    CONSTRAINT fk_txn_from FOREIGN KEY (from_account_id) REFERENCES accounts (account_id),
    CONSTRAINT fk_txn_to   FOREIGN KEY (to_account_id)   REFERENCES accounts (account_id),
    CONSTRAINT chk_txn_type    CHECK (txn_type IN ('deposit', 'withdrawal', 'transfer')),
    CONSTRAINT chk_txn_amount  CHECK (amount > 0),
    CONSTRAINT chk_txn_channel CHECK (channel IN ('online', 'atm', 'branch', 'upi', 'card')),
    CONSTRAINT chk_txn_status  CHECK (status IN ('completed', 'failed', 'reversed')),
    CONSTRAINT chk_txn_accounts CHECK (
        (txn_type = 'deposit'    AND from_account_id IS NULL     AND to_account_id IS NOT NULL) OR
        (txn_type = 'withdrawal' AND from_account_id IS NOT NULL AND to_account_id IS NULL)     OR
        (txn_type = 'transfer'   AND from_account_id IS NOT NULL AND to_account_id IS NOT NULL
                                 AND from_account_id <> to_account_id)
    )
) ENGINE = InnoDB;

-- Configurable fraud rules (the trigger reads these)
--   HIGH_AMOUNT      : amount >= threshold_amount
--   RAPID_TXN        : >= max_count outgoing transactions within window_minutes
--   UNUSUAL_LOCATION : transaction city differs from the customer's home_city
CREATE TABLE fraud_rules (
    rule_id          INT           NOT NULL AUTO_INCREMENT,
    rule_name        VARCHAR(80)   NOT NULL,
    rule_type        VARCHAR(20)   NOT NULL,
    threshold_amount DECIMAL(15,2) NULL,
    window_minutes   INT           NULL,
    max_count        INT           NULL,
    risk_score       INT           NOT NULL DEFAULT 50,
    is_active        TINYINT(1)    NOT NULL DEFAULT 1,
    PRIMARY KEY (rule_id),
    UNIQUE KEY uq_rule_name (rule_name),
    CONSTRAINT chk_rule_type  CHECK (rule_type IN ('HIGH_AMOUNT', 'RAPID_TXN', 'UNUSUAL_LOCATION')),
    CONSTRAINT chk_rule_score CHECK (risk_score BETWEEN 1 AND 100),
    CONSTRAINT chk_rule_params CHECK (
        (rule_type = 'HIGH_AMOUNT' AND threshold_amount IS NOT NULL) OR
        (rule_type = 'RAPID_TXN'   AND window_minutes IS NOT NULL AND max_count IS NOT NULL) OR
        (rule_type = 'UNUSUAL_LOCATION')
    )
) ENGINE = InnoDB;

-- A transaction can be flagged by several rules; analysts review each flag
CREATE TABLE fraud_flags (
    flag_id      INT         NOT NULL AUTO_INCREMENT,
    txn_id       BIGINT      NOT NULL,
    rule_id      INT         NOT NULL,
    risk_score   INT         NOT NULL,
    status       VARCHAR(12) NOT NULL DEFAULT 'open',
    flagged_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by  INT         NULL,
    reviewed_at  DATETIME    NULL,
    PRIMARY KEY (flag_id),
    UNIQUE KEY uq_flag_txn_rule (txn_id, rule_id),
    KEY idx_flags_status   (status),
    KEY idx_flags_rule     (rule_id),
    KEY idx_flags_reviewer (reviewed_by),
    CONSTRAINT fk_flag_txn      FOREIGN KEY (txn_id)      REFERENCES transactions (txn_id) ON DELETE CASCADE,
    CONSTRAINT fk_flag_rule     FOREIGN KEY (rule_id)     REFERENCES fraud_rules (rule_id),
    CONSTRAINT fk_flag_reviewer FOREIGN KEY (reviewed_by) REFERENCES users (user_id),
    CONSTRAINT chk_flag_status  CHECK (status IN ('open', 'confirmed', 'dismissed')),
    CONSTRAINT chk_flag_review  CHECK (status <> 'open' OR reviewed_by IS NULL)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 2. TRIGGER: automatic fraud detection after every transaction
-- ---------------------------------------------------------------------
DELIMITER $$

CREATE TRIGGER trg_check_fraud
AFTER INSERT ON transactions
FOR EACH ROW
BEGIN
    -- Only money leaving an account is scored (deposits have no sender).
    IF NEW.from_account_id IS NOT NULL THEN

        -- Rule type 1: unusually large amount
        INSERT INTO fraud_flags (txn_id, rule_id, risk_score, flagged_at)
        SELECT NEW.txn_id, r.rule_id, r.risk_score, NEW.created_at
          FROM fraud_rules r
         WHERE r.is_active = 1
           AND r.rule_type = 'HIGH_AMOUNT'
           AND NEW.amount >= r.threshold_amount
         ORDER BY r.rule_id;

        -- Rule type 2: too many outgoing transactions in a short window
        INSERT INTO fraud_flags (txn_id, rule_id, risk_score, flagged_at)
        SELECT NEW.txn_id, r.rule_id, r.risk_score, NEW.created_at
          FROM fraud_rules r
         WHERE r.is_active = 1
           AND r.rule_type = 'RAPID_TXN'
           AND (SELECT COUNT(*)
                  FROM transactions t
                 WHERE t.from_account_id = NEW.from_account_id
                   AND t.created_at <= NEW.created_at
                   AND t.created_at >= NEW.created_at - INTERVAL r.window_minutes MINUTE
               ) >= r.max_count
         ORDER BY r.rule_id;

        -- Rule type 3: transaction city differs from the customer's home city
        IF NEW.location_city IS NOT NULL THEN
            INSERT INTO fraud_flags (txn_id, rule_id, risk_score, flagged_at)
            SELECT NEW.txn_id, r.rule_id, r.risk_score, NEW.created_at
              FROM fraud_rules r
              JOIN accounts  a ON a.account_id  = NEW.from_account_id
              JOIN customers c ON c.customer_id = a.customer_id
             WHERE r.is_active = 1
               AND r.rule_type = 'UNUSUAL_LOCATION'
               AND LOWER(c.home_city) <> LOWER(NEW.location_city)
             ORDER BY r.rule_id;
        END IF;
    END IF;
END$$

-- ---------------------------------------------------------------------
-- 3. STORED PROCEDURE: atomic money transfer (TRANSACTION demo)
--    Any error rolls back the debit, the credit and the log row together.
-- ---------------------------------------------------------------------
CREATE PROCEDURE transfer_funds(
    IN p_from    INT,
    IN p_to      INT,
    IN p_amount  DECIMAL(15,2),
    IN p_channel VARCHAR(10),
    IN p_city    VARCHAR(60),
    IN p_desc    TEXT
)
BEGIN
    DECLARE v_found       INT DEFAULT 0;
    DECLARE v_balance     DECIMAL(15,2);
    DECLARE v_from_status VARCHAR(10);
    DECLARE v_to_status   VARCHAR(10);
    DECLARE v_txn_id      BIGINT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_from = p_to THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot transfer to the same account';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Amount must be greater than zero';
    END IF;

    START TRANSACTION;

    -- Take the row locks in a fixed (id) order so two opposite transfers cannot deadlock
    UPDATE accounts SET balance = balance WHERE account_id = LEAST(p_from, p_to);
    UPDATE accounts SET balance = balance WHERE account_id = GREATEST(p_from, p_to);

    SELECT COUNT(*) INTO v_found FROM accounts WHERE account_id IN (p_from, p_to);
    IF v_found < 2 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Source or destination account not found';
    END IF;

    SELECT balance, status INTO v_balance, v_from_status FROM accounts WHERE account_id = p_from;
    SELECT status INTO v_to_status FROM accounts WHERE account_id = p_to;

    IF v_from_status <> 'active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Source account is not active';
    END IF;
    IF v_to_status <> 'active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Destination account is not active';
    END IF;
    IF v_balance < p_amount THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Insufficient funds';
    END IF;

    UPDATE accounts SET balance = balance - p_amount WHERE account_id = p_from;
    UPDATE accounts SET balance = balance + p_amount WHERE account_id = p_to;

    INSERT INTO transactions (from_account_id, to_account_id, txn_type, amount,
                              channel, location_city, description)
    VALUES (p_from, p_to, 'transfer', p_amount, p_channel, p_city, p_desc);
    SET v_txn_id = LAST_INSERT_ID();

    COMMIT;

    SELECT v_txn_id AS txn_id;
END$$

-- ---------------------------------------------------------------------
-- 3b. STORED PROCEDURE: add funds to an account (simulated deposit for the demo)
-- ---------------------------------------------------------------------
CREATE PROCEDURE deposit_funds(
    IN p_account INT,
    IN p_amount  DECIMAL(15,2),
    IN p_channel VARCHAR(10),
    IN p_city    VARCHAR(60),
    IN p_desc    TEXT
)
BEGIN
    DECLARE v_found  INT DEFAULT 0;
    DECLARE v_status VARCHAR(10);
    DECLARE v_txn_id BIGINT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_amount IS NULL OR p_amount <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Amount must be greater than zero';
    END IF;

    START TRANSACTION;

    UPDATE accounts SET balance = balance WHERE account_id = p_account;   -- take the row lock

    SELECT COUNT(*) INTO v_found FROM accounts WHERE account_id = p_account;
    IF v_found < 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Account not found';
    END IF;

    SELECT status INTO v_status FROM accounts WHERE account_id = p_account;
    IF v_status <> 'active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Account is not active';
    END IF;

    UPDATE accounts SET balance = balance + p_amount WHERE account_id = p_account;

    INSERT INTO transactions (from_account_id, to_account_id, txn_type, amount,
                              channel, location_city, description)
    VALUES (NULL, p_account, 'deposit', p_amount, p_channel, p_city, p_desc);
    SET v_txn_id = LAST_INSERT_ID();

    COMMIT;

    SELECT v_txn_id AS txn_id;
END$$

DELIMITER ;

-- ---------------------------------------------------------------------
-- 4. VIEWS
-- ---------------------------------------------------------------------
CREATE VIEW v_suspicious_transactions AS
SELECT f.flag_id, f.status AS flag_status, f.risk_score, f.flagged_at,
       t.txn_id, t.txn_type, t.amount, t.channel, t.location_city,
       t.created_at AS txn_created_at,
       fr.rule_name,
       a.account_number AS from_account,
       c.full_name      AS customer_name
  FROM fraud_flags f
  JOIN transactions t   ON t.txn_id  = f.txn_id
  JOIN fraud_rules  fr  ON fr.rule_id = f.rule_id
  LEFT JOIN accounts  a ON a.account_id  = t.from_account_id
  LEFT JOIN customers c ON c.customer_id = a.customer_id;

CREATE VIEW v_account_summary AS
SELECT a.account_id, a.account_number, a.account_type, a.balance, a.status,
       c.full_name, b.branch_name,
       (SELECT COUNT(*) FROM transactions t WHERE t.from_account_id = a.account_id) AS total_outgoing,
       (SELECT COUNT(*) FROM transactions t WHERE t.to_account_id   = a.account_id) AS total_incoming
  FROM accounts a
  JOIN customers c ON c.customer_id = a.customer_id
  JOIN branches  b ON b.branch_id   = a.branch_id;

-- ---------------------------------------------------------------------
-- 5. DEFAULT FRAUD RULES
-- ---------------------------------------------------------------------
INSERT INTO fraud_rules (rule_name, rule_type, threshold_amount, window_minutes, max_count, risk_score) VALUES
 ('Large transaction (>= 50,000)',    'HIGH_AMOUNT',      50000.00, NULL, NULL, 70),
 ('Rapid transactions (5 in 10 min)', 'RAPID_TXN',        NULL,     10,   5,    80),
 ('Transaction outside home city',    'UNUSUAL_LOCATION', NULL,     NULL, NULL, 50);

-- ---------------------------------------------------------------------
-- 5b. REFERENCE DATA: branches (customers pick one when they open an account).
--     There are NO users, accounts or transactions: the system starts empty.
-- ---------------------------------------------------------------------
INSERT INTO branches (branch_name, ifsc_code, city) VALUES
 ('Mumbai Main',         'BANK0000001', 'Mumbai'),
 ('Delhi Central',       'BANK0000002', 'Delhi'),
 ('Bengaluru Tech Park', 'BANK0000003', 'Bengaluru'),
 ('Chennai Marina',      'BANK0000004', 'Chennai');

-- ---------------------------------------------------------------------
-- 6. ALTER / DROP demonstrations (for the DDL requirement; run by hand)
-- ---------------------------------------------------------------------
-- ALTER TABLE customers ADD COLUMN email_verified TINYINT(1) NOT NULL DEFAULT 0;
-- ALTER TABLE branches  MODIFY city VARCHAR(80) NOT NULL;
-- ALTER TABLE accounts  ADD INDEX idx_accounts_status (status);
-- DROP INDEX idx_txn_created ON transactions;
