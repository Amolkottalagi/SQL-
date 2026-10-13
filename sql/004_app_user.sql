-- Least-privilege account for the app itself (don't run the app as root).
-- Change the password, and put it in the DB_PASSWORD environment variable.
CREATE USER IF NOT EXISTS 'sqlllm_app'@'%' IDENTIFIED BY 'app_pw_change_me';
CREATE USER IF NOT EXISTS 'sqlllm_app'@'localhost' IDENTIFIED BY 'app_pw_change_me';
GRANT SELECT, INSERT, UPDATE, DELETE ON app_db.* TO 'sqlllm_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON app_db.* TO 'sqlllm_app'@'localhost';
FLUSH PRIVILEGES;
