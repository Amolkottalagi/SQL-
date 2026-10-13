USE app_db;

-- Evaluation / Research page. Keep test_questions separate from few_shot_examples
-- so the few-shot experiment is not scored on questions it was shown as examples.

CREATE TABLE IF NOT EXISTS test_questions (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  question_text TEXT NOT NULL,
  expected_sql  TEXT NOT NULL,
  category      VARCHAR(40) NULL,
  difficulty    ENUM('easy','medium','hard') NOT NULL DEFAULT 'easy',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experiment_runs (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  strategy            ENUM('zero_shot','few_shot','chain_of_thought') NOT NULL,
  prompt_template_id  INT NULL,
  model_name          VARCHAR(60) NOT NULL,
  total               INT NOT NULL DEFAULT 0,
  correct             INT NOT NULL DEFAULT 0,
  started_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_run_tpl FOREIGN KEY (prompt_template_id) REFERENCES prompt_templates(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS experiment_results (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id            INT NOT NULL,
  test_question_id  INT NOT NULL,
  generated_sql     TEXT NULL,
  is_correct        BOOLEAN NOT NULL,
  failure_type      ENUM('llm_error','blocked','sql_error','wrong_result') NULL,
  CONSTRAINT fk_res_run FOREIGN KEY (run_id) REFERENCES experiment_runs(id) ON DELETE CASCADE,
  CONSTRAINT fk_res_q   FOREIGN KEY (test_question_id) REFERENCES test_questions(id)
);
