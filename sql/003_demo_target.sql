-- Demo database the LLM answers questions about, plus a SELECT-only account for it.
-- The LLM connection must NEVER use app_db credentials (app_db holds password hashes and payments).
-- Run as a MySQL admin (root). Change the password before using outside local development.

CREATE DATABASE IF NOT EXISTS shop_db;
USE shop_db;

CREATE TABLE IF NOT EXISTS customers (
  id INT PRIMARY KEY, name VARCHAR(60) NOT NULL, city VARCHAR(40) NOT NULL, signup_date DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INT PRIMARY KEY, name VARCHAR(60) NOT NULL, category VARCHAR(40) NOT NULL, price DECIMAL(10,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INT PRIMARY KEY, customer_id INT NOT NULL, product_id INT NOT NULL,
  quantity INT NOT NULL, order_date DATE NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

INSERT IGNORE INTO customers VALUES
 (1,'Asha','Pune','2025-01-10'),(2,'Ravi','Mumbai','2025-02-14'),(3,'Meera','Pune','2025-03-01'),
 (4,'Kabir','Delhi','2025-03-20'),(5,'Isha','Mumbai','2025-04-05');
INSERT IGNORE INTO products VALUES
 (1,'Laptop','Electronics',55000),(2,'Phone','Electronics',25000),(3,'Desk','Furniture',8000),
 (4,'Chair','Furniture',4500),(5,'Notebook','Stationery',120);
INSERT IGNORE INTO orders VALUES
 (1,1,1,1,'2025-05-01'),(2,1,5,10,'2025-05-03'),(3,2,2,2,'2025-05-10'),(4,3,3,1,'2025-06-01'),
 (5,3,4,2,'2025-06-02'),(6,4,1,1,'2025-06-15'),(7,5,5,20,'2025-07-01'),(8,2,4,1,'2025-07-09'),
 (9,4,2,1,'2025-07-20');

CREATE USER IF NOT EXISTS 'llm_reader'@'%' IDENTIFIED BY 'reader_pw_change_me';
CREATE USER IF NOT EXISTS 'llm_reader'@'localhost' IDENTIFIED BY 'reader_pw_change_me';
GRANT SELECT ON shop_db.* TO 'llm_reader'@'%';
GRANT SELECT ON shop_db.* TO 'llm_reader'@'localhost';
FLUSH PRIVILEGES;
