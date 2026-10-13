CREATE DATABASE IF NOT EXISTS app_db;
USE app_db;

-- =====================================================================
-- 1. PLANS & USERS
-- =====================================================================

CREATE TABLE plans (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  name                VARCHAR(30)  NOT NULL UNIQUE,          -- free | pro | admin
  daily_query_limit   INT NULL,                              -- NULL = unlimited
  daily_token_limit   INT NULL,                              -- NULL = unlimited
  price_monthly       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  can_export_csv      BOOLEAN NOT NULL DEFAULT FALSE,
  can_use_charts      BOOLEAN NOT NULL DEFAULT FALSE,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
  id                    INT AUTO_INCREMENT PRIMARY KEY,
  username              VARCHAR(50)  NOT NULL UNIQUE,
  email                 VARCHAR(120) NOT NULL UNIQUE,
  password_hash         VARCHAR(255) NOT NULL,               -- bcrypt, never plain text
  role                  ENUM('user','admin') NOT NULL DEFAULT 'user',
  plan_id               INT NOT NULL DEFAULT 1,
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  consent_accepted_at   TIMESTAMP NULL,                      -- user accepted that questions are stored
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  last_login_at         TIMESTAMP NULL,
  CONSTRAINT fk_users_plan FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE TABLE user_settings (
  user_id             INT PRIMARY KEY,
  default_chart_type  ENUM('none','bar','line','pie') NOT NULL DEFAULT 'bar',
  theme               ENUM('light','dark') NOT NULL DEFAULT 'light',
  rows_per_page       INT NOT NULL DEFAULT 50,
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_settings_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- =====================================================================
-- 2. AUTHENTICATION & SECURITY
-- =====================================================================

CREATE TABLE auth_sessions (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NOT NULL,
  session_token_hash  CHAR(64) NOT NULL UNIQUE,              -- SHA-256 of the token
  ip_address          VARCHAR(45) NULL,
  user_agent          VARCHAR(255) NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at          TIMESTAMP NOT NULL,
  revoked_at          TIMESTAMP NULL,
  CONSTRAINT fk_authsess_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_authsess_user (user_id, expires_at)
);

CREATE TABLE password_reset_tokens (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NOT NULL,
  token_hash          CHAR(64) NOT NULL UNIQUE,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at          TIMESTAMP NOT NULL,
  used_at             TIMESTAMP NULL,
  CONSTRAINT fk_pwreset_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE login_attempts (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  username_tried      VARCHAR(120) NOT NULL,
  user_id             INT NULL,                              -- NULL if username does not exist
  ip_address          VARCHAR(45) NULL,
  success             BOOLEAN NOT NULL,
  attempted_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_login_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_login_ip_time (ip_address, attempted_at),
  INDEX idx_login_user_time (username_tried, attempted_at)
);

-- =====================================================================
-- 3. APP CONFIGURATION (prompts, examples, schema cache)
-- =====================================================================

CREATE TABLE prompt_templates (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  name                VARCHAR(60) NOT NULL,
  version             INT NOT NULL DEFAULT 1,
  strategy            ENUM('zero_shot','few_shot','chain_of_thought') NOT NULL DEFAULT 'few_shot',
  template_text       MEDIUMTEXT NOT NULL,                   -- placeholders: {schema}, {examples}, {question}
  is_active           BOOLEAN NOT NULL DEFAULT FALSE,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_template_version (name, version)
);

CREATE TABLE few_shot_examples (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  question_text       TEXT NOT NULL,
  sql_text            TEXT NOT NULL,
  difficulty          ENUM('easy','medium','hard') NOT NULL DEFAULT 'easy',
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE schema_cache (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  database_name       VARCHAR(64) NOT NULL UNIQUE,
  schema_text         MEDIUMTEXT NOT NULL,                   -- tables, columns, types, FKs, sample rows
  schema_hash         CHAR(64) NOT NULL,                     -- detect when the target DB changed
  refreshed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- =====================================================================
-- 4. CHAT: SESSIONS, PROMPTS, RESPONSES, FEEDBACK
-- =====================================================================
-- user_id uses ON DELETE SET NULL so history can be anonymised (kept for
-- research) when an account is deleted.

CREATE TABLE chat_sessions (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NULL,
  title               VARCHAR(150) NOT NULL DEFAULT 'New chat',
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_chat_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_chat_user_updated (user_id, updated_at)
);

CREATE TABLE prompts (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  chat_session_id     BIGINT NOT NULL,
  user_id             INT NULL,
  question_text       TEXT NOT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_prompt_chat FOREIGN KEY (chat_session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
  CONSTRAINT fk_prompt_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_prompt_user_date (user_id, created_at),
  INDEX idx_prompt_chat (chat_session_id)
);

CREATE TABLE responses (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  prompt_id           BIGINT NOT NULL,
  prompt_template_id  INT NULL,                              -- which prompt version produced this
  attempt_no          TINYINT NOT NULL DEFAULT 1,            -- 1 = first try, 2+ = self-correction retries
  model_name          VARCHAR(60) NOT NULL,
  generated_sql       TEXT NULL,
  explanation         TEXT NULL,
  status              ENUM('success','error','blocked','clarification') NOT NULL,
  error_message       TEXT NULL,
  row_count           INT NULL,
  latency_ms          INT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_resp_prompt   FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
  CONSTRAINT fk_resp_template FOREIGN KEY (prompt_template_id) REFERENCES prompt_templates(id) ON DELETE SET NULL,
  INDEX idx_resp_prompt (prompt_id),
  INDEX idx_resp_status_date (status, created_at)
);

CREATE TABLE feedback (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  response_id         BIGINT NOT NULL,
  user_id             INT NULL,
  rating              TINYINT NOT NULL,                      -- 1 = thumbs up, -1 = thumbs down
  reason              ENUM('wrong_result','wrong_sql','slow','confusing','other') NULL,
  comment             TEXT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_fb_resp FOREIGN KEY (response_id) REFERENCES responses(id) ON DELETE CASCADE,
  CONSTRAINT fk_fb_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT chk_fb_rating CHECK (rating IN (-1, 1)),
  UNIQUE KEY uq_fb_one_per_user (response_id, user_id)
);

CREATE TABLE saved_queries (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NOT NULL,
  title               VARCHAR(150) NOT NULL,
  question_text       TEXT NOT NULL,
  sql_text            TEXT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_saved_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_saved_user (user_id)
);

-- =====================================================================
-- 5. TOKENS, PAYMENTS, INVOICES
-- =====================================================================

CREATE TABLE token_usage (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NULL,
  response_id         BIGINT NULL,                           -- NULL for calls without a stored response
  prompt_tokens       INT NOT NULL DEFAULT 0,
  output_tokens       INT NOT NULL DEFAULT 0,
  total_tokens        INT NOT NULL DEFAULT 0,                -- from Gemini usage_metadata
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_tok_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_tok_resp FOREIGN KEY (response_id) REFERENCES responses(id) ON DELETE SET NULL,
  INDEX idx_tok_user_date (user_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE token_balances (
  user_id             INT PRIMARY KEY,
  balance             BIGINT NOT NULL DEFAULT 0,             -- purchased tokens remaining
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_bal_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT chk_bal_nonneg CHECK (balance >= 0)
) ENGINE=InnoDB;

CREATE TABLE payments (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id               INT NOT NULL,                        -- no cascade: financial records are kept
  plan_id               INT NULL,                            -- set when buying a plan
  tokens_purchased      BIGINT NOT NULL DEFAULT 0,           -- set when buying a token pack
  amount                DECIMAL(10,2) NOT NULL,
  currency              CHAR(3) NOT NULL DEFAULT 'INR',
  gateway               VARCHAR(30) NOT NULL DEFAULT 'razorpay',
  gateway_order_id      VARCHAR(100) NOT NULL UNIQUE,
  gateway_payment_id    VARCHAR(100) NULL,                   -- NEVER store card details
  status                ENUM('created','pending','success','failed','refunded') NOT NULL DEFAULT 'created',
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at               TIMESTAMP NULL,
  CONSTRAINT fk_pay_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_pay_plan FOREIGN KEY (plan_id) REFERENCES plans(id),
  INDEX idx_pay_user_date (user_id, created_at)
);

CREATE TABLE invoices (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  payment_id          BIGINT NOT NULL UNIQUE,
  invoice_number      VARCHAR(30) NOT NULL UNIQUE,
  subtotal            DECIMAL(10,2) NOT NULL,
  tax_amount          DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total               DECIMAL(10,2) NOT NULL,
  issued_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_inv_pay FOREIGN KEY (payment_id) REFERENCES payments(id)
);

-- =====================================================================
-- 6. SECURITY AUDIT & OPERATIONS
-- =====================================================================

CREATE TABLE blocked_queries (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NULL,
  prompt_id           BIGINT NULL,
  question_text       TEXT NULL,
  attempted_sql       TEXT NULL,
  reason              ENUM('non_select','prompt_injection','unknown_table','timeout','other') NOT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_blk_user   FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE SET NULL,
  CONSTRAINT fk_blk_prompt FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
  INDEX idx_blk_user_date (user_id, created_at)
);

CREATE TABLE audit_logs (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  admin_id            INT NULL,
  action              VARCHAR(60) NOT NULL,                  -- e.g. change_plan, delete_user, refund
  target_type         VARCHAR(30) NULL,                      -- e.g. user, payment
  target_id           BIGINT NULL,
  details             JSON NULL,
  ip_address          VARCHAR(45) NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_audit_admin FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_audit_date (created_at)
);

CREATE TABLE error_logs (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NULL,
  source              ENUM('gemini','database','app','payment') NOT NULL,
  error_code          VARCHAR(50) NULL,                      -- e.g. 429 for rate limit
  message             TEXT NOT NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_err_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_err_source_date (source, created_at)
);

CREATE TABLE notifications (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id             INT NOT NULL,
  type                ENUM('limit_reached','payment_success','payment_failed','plan_changed','info') NOT NULL,
  message             VARCHAR(255) NOT NULL,
  is_read             BOOLEAN NOT NULL DEFAULT FALSE,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_notif_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_notif_user_read (user_id, is_read)
);

CREATE TABLE daily_usage_summary (
  user_id             INT NOT NULL,
  usage_date          DATE NOT NULL,
  query_count         INT NOT NULL DEFAULT 0,
  error_count         INT NOT NULL DEFAULT 0,
  total_tokens        BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, usage_date),
  CONSTRAINT fk_sum_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
