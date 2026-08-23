CREATE SCHEMA analytics;

CREATE TABLE analytics.departments (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    arabic_name VARCHAR NOT NULL,
    cost_center VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE analytics.employees (
    id INTEGER PRIMARY KEY,
    employee_number VARCHAR NOT NULL,
    department_id INTEGER NOT NULL,
    manager_id INTEGER,
    full_name VARCHAR NOT NULL,
    arabic_name VARCHAR NOT NULL,
    job_title VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    hire_date DATE NOT NULL,
    termination_date DATE,
    salary DECIMAL(12, 2) NOT NULL,
    currency VARCHAR NOT NULL
);

CREATE TABLE analytics.payroll (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    base_salary DECIMAL(12, 2) NOT NULL,
    bonus DECIMAL(12, 2) NOT NULL,
    deductions DECIMAL(12, 2) NOT NULL,
    paid_at DATE,
    status VARCHAR NOT NULL
);

CREATE TABLE analytics.customers (
    id INTEGER PRIMARY KEY,
    customer_code VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    arabic_name VARCHAR,
    country_code VARCHAR NOT NULL,
    industry VARCHAR NOT NULL,
    status VARCHAR NOT NULL
);

CREATE TABLE analytics.projects (
    id INTEGER PRIMARY KEY,
    project_code VARCHAR NOT NULL,
    customer_id INTEGER NOT NULL,
    owning_department_id INTEGER NOT NULL,
    name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    budget DECIMAL(14, 2) NOT NULL
);

CREATE TABLE analytics.employee_project_assignments (
    employee_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    assigned_from DATE NOT NULL,
    assigned_to DATE,
    allocation_percent DECIMAL(5, 2) NOT NULL,
    billable BOOLEAN NOT NULL
);

CREATE TABLE analytics.invoices (
    id INTEGER PRIMARY KEY,
    invoice_number VARCHAR NOT NULL,
    customer_id INTEGER NOT NULL,
    project_id INTEGER,
    issued_on DATE NOT NULL,
    due_on DATE NOT NULL,
    status VARCHAR NOT NULL,
    currency VARCHAR NOT NULL
);

CREATE TABLE analytics.invoice_lines (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    description VARCHAR NOT NULL,
    quantity DECIMAL(10, 2) NOT NULL,
    unit_price DECIMAL(12, 2) NOT NULL
);

CREATE TABLE analytics.project_costs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    cost_date DATE NOT NULL,
    category VARCHAR NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    description VARCHAR NOT NULL
);

INSERT INTO analytics.departments VALUES
    (1, 'Engineering', 'الهندسة', 'CC-100', '2020-01-01'),
    (2, 'Sales', 'المبيعات', 'CC-200', '2020-01-01'),
    (3, 'Finance', 'المالية', 'CC-300', '2020-01-01'),
    (4, 'People Operations', 'الموارد البشرية', 'CC-400', '2020-01-01');

INSERT INTO analytics.employees VALUES
    (1, 'E-1001', 1, NULL, 'Maya Haddad', 'مايا حداد', 'VP Engineering', 'active', '2019-02-10', NULL, 190000, 'USD'),
    (2, 'E-1002', 1, 1, 'Karim Adel', 'كريم عادل', 'Staff Engineer', 'active', '2020-06-15', NULL, 160000, 'USD'),
    (3, 'E-1003', 1, 1, 'Lina Saleh', 'لينا صالح', 'Senior Engineer', 'active', '2021-03-01', NULL, 140000, 'USD'),
    (4, 'E-1004', 1, 1, 'Adam Nasser', 'آدم ناصر', 'Software Engineer', 'active', '2023-01-09', NULL, 120000, 'USD'),
    (5, 'E-2001', 2, NULL, 'Noura Mansour', 'نورا منصور', 'Sales Director', 'active', '2018-09-12', NULL, 150000, 'USD'),
    (6, 'E-2002', 2, 5, 'Youssef Amin', 'يوسف أمين', 'Account Executive', 'active', '2021-11-20', NULL, 120000, 'USD'),
    (7, 'E-2003', 2, 5, 'Sara Ibrahim', 'سارة إبراهيم', 'Account Executive', 'active', '2022-04-18', NULL, 105000, 'USD'),
    (8, 'E-3001', 3, NULL, 'Omar Farouk', 'عمر فاروق', 'Finance Director', 'active', '2017-05-22', NULL, 145000, 'USD'),
    (9, 'E-3002', 3, 8, 'Hana Samir', 'هنا سمير', 'Financial Analyst', 'active', '2022-08-07', NULL, 110000, 'USD'),
    (10, 'E-4001', 4, NULL, 'Dalia Fawzi', 'داليا فوزي', 'People Director', 'active', '2020-02-02', NULL, 135000, 'USD'),
    (11, 'E-4002', 4, 10, 'Tarek Kamal', 'طارق كمال', 'Recruiter', 'leave', '2022-10-10', NULL, 90000, 'USD'),
    (12, 'E-1999', 1, 1, 'Former Employee', 'موظف سابق', 'Engineer', 'terminated', '2018-01-01', '2023-12-31', 100000, 'USD');

INSERT INTO analytics.payroll
SELECT
    row_number() OVER ()::INTEGER,
    id,
    DATE '2025-01-01',
    DATE '2025-01-31',
    salary / 12,
    0,
    salary / 120,
    DATE '2025-01-31',
    'paid'
FROM analytics.employees
WHERE status <> 'terminated';

INSERT INTO analytics.payroll
SELECT
    100 + row_number() OVER ()::INTEGER,
    id,
    DATE '2025-02-01',
    DATE '2025-02-28',
    salary / 12,
    CASE WHEN department_id = 2 THEN 1500 ELSE 0 END,
    salary / 120,
    DATE '2025-02-28',
    'paid'
FROM analytics.employees
WHERE status <> 'terminated';

INSERT INTO analytics.customers VALUES
    (1, 'C-001', 'Nile Retail Group', 'مجموعة النيل للتجزئة', 'EG', 'Retail', 'active'),
    (2, 'C-002', 'Gulf Logistics', 'الخليج للخدمات اللوجستية', 'AE', 'Logistics', 'active'),
    (3, 'C-003', 'Atlas Manufacturing', NULL, 'MA', 'Manufacturing', 'inactive');

INSERT INTO analytics.projects VALUES
    (1, 'P-101', 1, 1, 'Retail Analytics Modernization', 'active', '2024-09-01', NULL, 480000),
    (2, 'P-102', 2, 1, 'Fleet Optimization Platform', 'active', '2025-01-15', NULL, 620000),
    (3, 'P-099', 3, 1, 'Factory Data Foundation', 'completed', '2023-03-01', '2024-04-30', 310000);

INSERT INTO analytics.employee_project_assignments VALUES
    (2, 1, '2024-09-01', NULL, 60, true),
    (3, 1, '2024-09-01', NULL, 80, true),
    (4, 2, '2025-01-15', NULL, 100, true),
    (2, 2, '2025-01-15', NULL, 30, true),
    (3, 3, '2023-03-01', '2024-04-30', 50, true);

INSERT INTO analytics.invoices VALUES
    (1, 'INV-2025-001', 1, 1, '2025-01-31', '2025-03-02', 'paid', 'USD'),
    (2, 'INV-2025-002', 2, 2, '2025-02-28', '2025-03-30', 'issued', 'USD');

INSERT INTO analytics.invoice_lines VALUES
    (1, 1, 'January delivery milestone', 1, 85000),
    (2, 1, 'Architecture workshop', 4, 2500),
    (3, 2, 'Discovery and platform setup', 1, 110000);

INSERT INTO analytics.project_costs VALUES
    (1, 1, '2025-01-31', 'labor', 42000, 'January delivery team'),
    (2, 1, '2025-01-20', 'software', 7500, 'Analytics platform licenses'),
    (3, 2, '2025-02-28', 'labor', 35000, 'February engineering effort'),
    (4, 2, '2025-02-18', 'travel', 4200, 'Customer discovery workshop');
