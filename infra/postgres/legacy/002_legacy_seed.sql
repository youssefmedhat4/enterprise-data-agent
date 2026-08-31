-- Deterministic seed for the legacy ERP fixture.
--
-- Every value is derived arithmetically from a row number, so a rebuild always
-- produces byte-identical data and canonical reference results never drift.
-- Nothing here is random.
--
-- The data deliberately contains the conditions a real legacy extract has:
-- historical compensation rows alongside the one in force, terminated staff who
-- still carry salary history, void invoices, reversed and unposted ledger
-- postings, projects with no billing, and display names that repeat across
-- different codes.

-- ---------------------------------------------------------------------------
-- Employees: 1001..1060
-- ---------------------------------------------------------------------------
-- Status spread by position: roughly two thirds active, the rest on leave,
-- inactive, or terminated.
INSERT INTO erp.emp_mst (
    emp_no, emp_nm, org_cd, stat_cd, hire_dt_chr, mgr_emp_no, term_dt_chr,
    rec_active_flg
)
SELECT
    1000 + n,
    'Employee ' || lpad(n::text, 3, '0'),
    (ARRAY['OU1000','OU1100','OU2100','OU2200','OU3000',
           'OU3100','OU4000','OU4100','OU2000'])[1 + (n % 9)],
    CASE
        WHEN n % 10 = 0 THEN 'T'
        WHEN n % 10 = 7 THEN 'L'
        WHEN n % 10 = 9 THEN 'I'
        ELSE 'A'
    END,
    to_char(DATE '2015-01-01' + (n * 37), 'YYYYMMDD'),
    CASE WHEN n <= 6 THEN NULL ELSE 1000 + (1 + (n % 6)) END,
    CASE WHEN n % 10 = 0
         THEN to_char(DATE '2023-03-01' + (n * 5), 'YYYYMMDD')
         ELSE NULL END,
    CASE WHEN n % 10 = 0 THEN 'N' ELSE 'Y' END
FROM generate_series(1, 60) AS n;

-- ---------------------------------------------------------------------------
-- Compensation history
-- ---------------------------------------------------------------------------
-- Superseded rows. Every employee has one or two of these, all curr_flg = 'N'.
-- Summing this table is the central trap: it is history, not payroll.
INSERT INTO erp.emp_comp_hist (
    emp_no, eff_dt_chr, ann_sal_amt, bonus_tgt_amt, curr_flg, ccy_cd
)
SELECT
    1000 + n,
    to_char(DATE '2019-01-01' + (n * 11), 'YYYYMMDD'),
    60000 + (n % 12) * 5000,
    2000 + (n % 5) * 500,
    'N',
    'USD'
FROM generate_series(1, 60) AS n;

INSERT INTO erp.emp_comp_hist (
    emp_no, eff_dt_chr, ann_sal_amt, bonus_tgt_amt, curr_flg, ccy_cd
)
SELECT
    1000 + n,
    to_char(DATE '2021-06-01' + (n * 7), 'YYYYMMDD'),
    70000 + (n % 12) * 5000,
    2500 + (n % 5) * 500,
    'N',
    'USD'
FROM generate_series(1, 60) AS n
WHERE n % 3 <> 0;

-- The rows in force. Employee 1041 is deliberately omitted: a real extract
-- always has someone whose current record never loaded.
INSERT INTO erp.emp_comp_hist (
    emp_no, eff_dt_chr, ann_sal_amt, bonus_tgt_amt, curr_flg, ccy_cd
)
SELECT
    1000 + n,
    to_char(DATE '2024-01-01' + (n * 3), 'YYYYMMDD'),
    80000 + (n % 12) * 5000,
    3000 + (n % 5) * 500,
    'Y',
    'USD'
FROM generate_series(1, 60) AS n
WHERE n <> 41;

-- ---------------------------------------------------------------------------
-- Projects: 5001..5040
-- ---------------------------------------------------------------------------
-- Two pairs of near-identical display names sit on different project numbers.
INSERT INTO erp.prj_hdr (
    prj_no, prj_nm, cust_cd, own_org_cd, stat_cd, start_dt_chr, end_dt_chr,
    budget_amt, ccy_cd, closed_flg
)
SELECT
    5000 + n,
    CASE n
        WHEN 7  THEN 'Atlas Migration'
        WHEN 8  THEN 'Atlas Migration Phase 2'
        WHEN 19 THEN 'Orion Rollout'
        WHEN 20 THEN 'Orion Rollout EMEA'
        ELSE 'Project ' || lpad(n::text, 3, '0')
    END,
    'C' || lpad((1 + (n % 22))::text, 4, '0'),
    (ARRAY['OU1000','OU1100','OU2100','OU2200',
           'OU4000','OU4100','OU3000'])[1 + (n % 7)],
    CASE WHEN n % 8 = 0 THEN 'CLS' WHEN n % 11 = 0 THEN 'HLD' ELSE 'OPN' END,
    to_char(DATE '2023-01-10' + (n * 9), 'YYYYMMDD'),
    CASE WHEN n % 8 = 0
         THEN to_char(DATE '2024-06-01' + (n * 4), 'YYYYMMDD')
         ELSE NULL END,
    100000 + (n % 9) * 25000,
    'USD',
    CASE WHEN n % 8 = 0 THEN 'Y' ELSE 'N' END
FROM generate_series(1, 40) AS n;

-- ---------------------------------------------------------------------------
-- Invoices
-- ---------------------------------------------------------------------------
-- Projects where n % 6 = 0 are never billed, so some projects have no invoices
-- at all. Every third invoice is voided.
INSERT INTO erp.ar_inv_hdr (inv_no, prj_no, inv_dt_chr, stat_cd, void_flg, ccy_cd)
SELECT
    9000 + ((n - 1) * 3) + s,
    5000 + n,
    to_char(DATE '2024-02-01' + (n * 6) + (s * 20), 'YYYYMMDD'),
    CASE WHEN (n + s) % 3 = 0 THEN 'VOI' WHEN (n + s) % 2 = 0 THEN 'PD'
         ELSE 'ISS' END,
    CASE WHEN (n + s) % 3 = 0 THEN 'Y' ELSE 'N' END,
    'USD'
FROM generate_series(1, 40) AS n
CROSS JOIN generate_series(1, 3) AS s
WHERE n % 6 <> 0;

-- Four detail lines per header, so any query that joins headers to lines and
-- also aggregates something else per header will fan out unless it aggregates
-- to the right grain first.
INSERT INTO erp.ar_inv_ln (inv_no, line_no, item_desc, qty, unit_amt, disc_amt)
SELECT
    h.inv_no,
    l,
    (ARRAY['Consulting hours','Licence fee','Implementation',
           'Support retainer'])[l],
    (1 + (h.inv_no % 4))::numeric,
    (500 + ((h.inv_no + l) % 7) * 250)::numeric,
    CASE WHEN (h.inv_no + l) % 5 = 0 THEN 100 ELSE 0 END
FROM erp.ar_inv_hdr AS h
CROSS JOIN generate_series(1, 4) AS l;

-- ---------------------------------------------------------------------------
-- Cost transactions
-- ---------------------------------------------------------------------------
-- Projects where n % 7 = 0 carry no postings. Every fifth posting is a
-- reversal and every ninth was never posted; neither should count.
INSERT INTO erp.gl_cost_txn (
    txn_no, prj_no, txn_dt_chr, cost_amt, txn_type_cd, reversal_flg, posted_flg
)
SELECT
    70000 + ((n - 1) * 10) + s,
    5000 + n,
    to_char(DATE '2024-01-15' + (n * 5) + (s * 11), 'YYYYMMDD'),
    (1500 + ((n + s) % 8) * 750)::numeric,
    (ARRAY['LAB','MAT','SUB','EXP'])[1 + ((n + s) % 4)],
    CASE WHEN (n + s) % 5 = 0 THEN 'Y' ELSE 'N' END,
    CASE WHEN (n + s) % 9 = 0 THEN 'N' ELSE 'Y' END
FROM generate_series(1, 40) AS n
CROSS JOIN generate_series(1, 10) AS s
WHERE n % 7 <> 0;

-- ---------------------------------------------------------------------------
-- Read-only role, mirroring the primary analytics database.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'erp_readonly') THEN
        EXECUTE format(
            'CREATE ROLE erp_readonly LOGIN PASSWORD %L',
            current_setting('erp.readonly_password', true)
        );
    END IF;
END
$$;

-- The gateway refuses a role that is not demonstrably read-only, so this is
-- required rather than belt-and-braces.
ALTER ROLE erp_readonly SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE legacy_erp TO erp_readonly;
GRANT USAGE ON SCHEMA erp TO erp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA erp TO erp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA erp GRANT SELECT ON TABLES TO erp_readonly;
