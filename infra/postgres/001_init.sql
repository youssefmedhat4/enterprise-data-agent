BEGIN;

CREATE SCHEMA analytics;

CREATE TABLE analytics.departments (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE,
    arabic_name text NOT NULL,
    cost_center text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analytics.employees (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_number text NOT NULL UNIQUE,
    department_id integer NOT NULL REFERENCES analytics.departments(id),
    manager_id integer REFERENCES analytics.employees(id),
    full_name text NOT NULL,
    arabic_name text NOT NULL,
    job_title text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'leave', 'terminated')),
    hire_date date NOT NULL,
    termination_date date,
    salary numeric(12, 2) NOT NULL CHECK (salary >= 0),
    currency char(3) NOT NULL DEFAULT 'USD',
    CHECK (termination_date IS NULL OR termination_date >= hire_date)
);

CREATE TABLE analytics.payroll (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id integer NOT NULL REFERENCES analytics.employees(id),
    period_start date NOT NULL,
    period_end date NOT NULL,
    base_salary numeric(12, 2) NOT NULL,
    bonus numeric(12, 2) NOT NULL DEFAULT 0,
    deductions numeric(12, 2) NOT NULL DEFAULT 0,
    paid_at date,
    status text NOT NULL CHECK (status IN ('draft', 'approved', 'paid')),
    UNIQUE (employee_id, period_start)
);

CREATE TABLE analytics.customers (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_code text NOT NULL UNIQUE,
    name text NOT NULL,
    arabic_name text,
    country_code char(2) NOT NULL,
    industry text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE analytics.projects (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_code text NOT NULL UNIQUE,
    customer_id integer NOT NULL REFERENCES analytics.customers(id),
    owning_department_id integer NOT NULL REFERENCES analytics.departments(id),
    name text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
    start_date date NOT NULL,
    end_date date,
    budget numeric(14, 2) NOT NULL CHECK (budget >= 0)
);

CREATE TABLE analytics.employee_project_assignments (
    employee_id integer NOT NULL REFERENCES analytics.employees(id),
    project_id integer NOT NULL REFERENCES analytics.projects(id),
    assigned_from date NOT NULL,
    assigned_to date,
    allocation_percent numeric(5, 2) NOT NULL CHECK (
        allocation_percent > 0 AND allocation_percent <= 100
    ),
    billable boolean NOT NULL,
    PRIMARY KEY (employee_id, project_id, assigned_from)
);

CREATE TABLE analytics.invoices (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_number text NOT NULL UNIQUE,
    customer_id integer NOT NULL REFERENCES analytics.customers(id),
    project_id integer REFERENCES analytics.projects(id),
    issued_on date NOT NULL,
    due_on date NOT NULL,
    status text NOT NULL CHECK (status IN ('draft', 'issued', 'paid', 'overdue', 'void')),
    currency char(3) NOT NULL DEFAULT 'USD'
);

CREATE TABLE analytics.invoice_lines (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id bigint NOT NULL REFERENCES analytics.invoices(id),
    description text NOT NULL,
    quantity numeric(10, 2) NOT NULL CHECK (quantity > 0),
    unit_price numeric(12, 2) NOT NULL CHECK (unit_price >= 0)
);

CREATE TABLE analytics.project_costs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id integer NOT NULL REFERENCES analytics.projects(id),
    cost_date date NOT NULL,
    category text NOT NULL CHECK (category IN ('labor', 'software', 'travel', 'vendor')),
    amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
    description text NOT NULL
);

INSERT INTO analytics.departments (name, arabic_name, cost_center) VALUES
    ('Engineering', 'الهندسة', 'CC-100'),
    ('Sales', 'المبيعات', 'CC-200'),
    ('Finance', 'المالية', 'CC-300'),
    ('People Operations', 'الموارد البشرية', 'CC-400');

INSERT INTO analytics.employees (
    employee_number, department_id, full_name, arabic_name, job_title, status, hire_date, salary
) VALUES
    ('E-1001', 1, 'Maya Haddad', 'مايا حداد', 'VP Engineering', 'active', '2019-02-10', 190000),
    ('E-1002', 1, 'Karim Adel', 'كريم عادل', 'Staff Engineer', 'active', '2020-06-15', 160000),
    ('E-1003', 1, 'Lina Saleh', 'لينا صالح', 'Senior Engineer', 'active', '2021-03-01', 140000),
    ('E-1004', 1, 'Adam Nasser', 'آدم ناصر', 'Software Engineer', 'active', '2023-01-09', 120000),
    ('E-2001', 2, 'Noura Mansour', 'نورا منصور', 'Sales Director', 'active', '2018-09-12', 150000),
    ('E-2002', 2, 'Youssef Amin', 'يوسف أمين', 'Account Executive', 'active', '2021-11-20', 120000),
    ('E-2003', 2, 'Sara Ibrahim', 'سارة إبراهيم', 'Account Executive', 'active', '2022-04-18', 105000),
    ('E-3001', 3, 'Omar Farouk', 'عمر فاروق', 'Finance Director', 'active', '2017-05-22', 145000),
    ('E-3002', 3, 'Hana Samir', 'هنا سمير', 'Financial Analyst', 'active', '2022-08-07', 110000),
    ('E-4001', 4, 'Dalia Fawzi', 'داليا فوزي', 'People Director', 'active', '2020-02-02', 135000),
    ('E-4002', 4, 'Tarek Kamal', 'طارق كمال', 'Recruiter', 'leave', '2022-10-10', 90000),
    ('E-1999', 1, 'Former Employee', 'موظف سابق', 'Engineer', 'terminated', '2018-01-01', 100000);

UPDATE analytics.employees SET termination_date = '2023-12-31' WHERE employee_number = 'E-1999';
UPDATE analytics.employees SET manager_id = 1 WHERE id IN (2, 3, 4, 12);
UPDATE analytics.employees SET manager_id = 5 WHERE id IN (6, 7);
UPDATE analytics.employees SET manager_id = 8 WHERE id = 9;
UPDATE analytics.employees SET manager_id = 10 WHERE id = 11;

INSERT INTO analytics.payroll (
    employee_id, period_start, period_end, base_salary, bonus, deductions, paid_at, status
)
SELECT id, '2025-01-01', '2025-01-31', salary / 12, 0, salary / 120, '2025-01-31', 'paid'
FROM analytics.employees WHERE status <> 'terminated';

INSERT INTO analytics.payroll (
    employee_id, period_start, period_end, base_salary, bonus, deductions, paid_at, status
)
SELECT id, '2025-02-01', '2025-02-28', salary / 12,
       CASE WHEN department_id = 2 THEN 1500 ELSE 0 END,
       salary / 120, '2025-02-28', 'paid'
FROM analytics.employees WHERE status <> 'terminated';

INSERT INTO analytics.customers (customer_code, name, arabic_name, country_code, industry, status) VALUES
    ('C-001', 'Nile Retail Group', 'مجموعة النيل للتجزئة', 'EG', 'Retail', 'active'),
    ('C-002', 'Gulf Logistics', 'الخليج للخدمات اللوجستية', 'AE', 'Logistics', 'active'),
    ('C-003', 'Atlas Manufacturing', NULL, 'MA', 'Manufacturing', 'inactive');

INSERT INTO analytics.projects (
    project_code, customer_id, owning_department_id, name, status, start_date, end_date, budget
) VALUES
    ('P-101', 1, 1, 'Retail Analytics Modernization', 'active', '2024-09-01', NULL, 480000),
    ('P-102', 2, 1, 'Fleet Optimization Platform', 'active', '2025-01-15', NULL, 620000),
    ('P-099', 3, 1, 'Factory Data Foundation', 'completed', '2023-03-01', '2024-04-30', 310000);

INSERT INTO analytics.employee_project_assignments (
    employee_id, project_id, assigned_from, assigned_to, allocation_percent, billable
) VALUES
    (2, 1, '2024-09-01', NULL, 60, true),
    (3, 1, '2024-09-01', NULL, 80, true),
    (4, 2, '2025-01-15', NULL, 100, true),
    (2, 2, '2025-01-15', NULL, 30, true),
    (3, 3, '2023-03-01', '2024-04-30', 50, true);

INSERT INTO analytics.invoices (
    invoice_number, customer_id, project_id, issued_on, due_on, status, currency
) VALUES
    ('INV-2025-001', 1, 1, '2025-01-31', '2025-03-02', 'paid', 'USD'),
    ('INV-2025-002', 2, 2, '2025-02-28', '2025-03-30', 'issued', 'USD');

INSERT INTO analytics.invoice_lines (invoice_id, description, quantity, unit_price) VALUES
    (1, 'January delivery milestone', 1, 85000),
    (1, 'Architecture workshop', 4, 2500),
    (2, 'Discovery and platform setup', 1, 110000);

INSERT INTO analytics.project_costs (project_id, cost_date, category, amount, description) VALUES
    (1, '2025-01-31', 'labor', 42000, 'January delivery team'),
    (1, '2025-01-20', 'software', 7500, 'Analytics platform licenses'),
    (2, '2025-02-28', 'labor', 35000, 'February engineering effort'),
    (2, '2025-02-18', 'travel', 4200, 'Customer discovery workshop');

COMMENT ON SCHEMA analytics IS 'Synthetic enterprise analytics data only.';
COMMENT ON TABLE analytics.departments IS 'Enterprise departments and cost centers.';
COMMENT ON TABLE analytics.employees IS 'Synthetic employee roster and annual base salaries.';
COMMENT ON TABLE analytics.payroll IS 'Historical monthly synthetic payroll facts.';

CREATE ROLE eda_readonly LOGIN PASSWORD :'eda_readonly_password';
ALTER ROLE eda_readonly SET default_transaction_read_only = on;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC;
GRANT CONNECT ON DATABASE enterprise_analytics TO eda_readonly;
GRANT USAGE ON SCHEMA analytics TO eda_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO eda_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO eda_readonly;

COMMIT;
