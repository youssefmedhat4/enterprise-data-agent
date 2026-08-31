-- Legacy ERP demo database.
--
-- A deliberately unfamiliar schema, shaped like something inherited from an
-- older accounting package: abbreviated names, status codes resolved through a
-- lookup table, dates kept as CHAR(8), Y/N flags, effective-dated compensation,
-- and header/detail invoices. Some logical relationships carry a real foreign
-- key and some deliberately do not, so schema discovery has to cope with both.
--
-- Nothing here is annotated with what a column "really means". Comments explain
-- storage conventions a DBA would document, never the business definition the
-- system is supposed to work out for itself.
--
-- All data is fixed rather than generated, so canonical reference results are
-- stable across rebuilds.

CREATE SCHEMA erp;

-- ---------------------------------------------------------------------------
-- Reference codes
-- ---------------------------------------------------------------------------

-- Shared code table, addressed by (domain_cd, value_cd). Older parts of the
-- system store the code only; the description lives here.
CREATE TABLE erp.code_lkp (
    domain_cd   varchar(12) NOT NULL,
    value_cd    varchar(4)  NOT NULL,
    value_desc  varchar(60) NOT NULL,
    active_flg  char(1)     NOT NULL DEFAULT 'Y',
    PRIMARY KEY (domain_cd, value_cd)
);

CREATE TABLE erp.org_unit_lkp (
    org_cd        varchar(8) PRIMARY KEY,
    org_nm        varchar(60) NOT NULL,
    region_cd     varchar(4),
    parent_org_cd varchar(8),
    active_flg    char(1) NOT NULL DEFAULT 'Y'
);

CREATE TABLE erp.cust_mst (
    cust_cd    varchar(8) PRIMARY KEY,
    cust_nm    varchar(80) NOT NULL,
    region_cd  varchar(4),
    active_flg char(1) NOT NULL DEFAULT 'Y'
);

-- ---------------------------------------------------------------------------
-- Workforce
-- ---------------------------------------------------------------------------

-- Dates are CHAR(8) YYYYMMDD, as written by the original COBOL loader.
CREATE TABLE erp.emp_mst (
    emp_no         integer PRIMARY KEY,
    emp_nm         varchar(80) NOT NULL,
    org_cd         varchar(8) REFERENCES erp.org_unit_lkp (org_cd),
    stat_cd        varchar(4) NOT NULL,
    hire_dt_chr    char(8),
    mgr_emp_no     integer,
    term_dt_chr    char(8),
    rec_active_flg char(1) NOT NULL DEFAULT 'Y'
);

-- One row per compensation change. The row in force carries curr_flg = 'Y'.
CREATE TABLE erp.emp_comp_hist (
    emp_no        integer NOT NULL REFERENCES erp.emp_mst (emp_no),
    eff_dt_chr    char(8) NOT NULL,
    ann_sal_amt   numeric(12, 2) NOT NULL,
    bonus_tgt_amt numeric(12, 2),
    curr_flg      char(1) NOT NULL DEFAULT 'N',
    ccy_cd        varchar(3) NOT NULL DEFAULT 'USD',
    PRIMARY KEY (emp_no, eff_dt_chr)
);

-- ---------------------------------------------------------------------------
-- Delivery
-- ---------------------------------------------------------------------------

-- cust_cd and own_org_cd are populated by an interface job that predates
-- referential integrity here; no constraint is declared on either.
CREATE TABLE erp.prj_hdr (
    prj_no       integer PRIMARY KEY,
    prj_nm       varchar(80) NOT NULL,
    cust_cd      varchar(8),
    own_org_cd   varchar(8),
    stat_cd      varchar(4) NOT NULL,
    start_dt_chr char(8),
    end_dt_chr   char(8),
    budget_amt   numeric(14, 2),
    ccy_cd       varchar(3) NOT NULL DEFAULT 'USD',
    closed_flg   char(1) NOT NULL DEFAULT 'N'
);

CREATE TABLE erp.ar_inv_hdr (
    inv_no     integer PRIMARY KEY,
    prj_no     integer REFERENCES erp.prj_hdr (prj_no),
    inv_dt_chr char(8),
    stat_cd    varchar(4) NOT NULL,
    void_flg   char(1) NOT NULL DEFAULT 'N',
    ccy_cd     varchar(3) NOT NULL DEFAULT 'USD'
);

CREATE TABLE erp.ar_inv_ln (
    inv_no    integer NOT NULL REFERENCES erp.ar_inv_hdr (inv_no),
    line_no   integer NOT NULL,
    item_desc varchar(80),
    qty       numeric(10, 2) NOT NULL DEFAULT 1,
    unit_amt  numeric(12, 2) NOT NULL DEFAULT 0,
    disc_amt  numeric(12, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (inv_no, line_no)
);

-- Ledger postings against a project. Reversals are kept as their own rows
-- rather than deleting the original.
CREATE TABLE erp.gl_cost_txn (
    txn_no       integer PRIMARY KEY,
    prj_no       integer,
    txn_dt_chr   char(8),
    cost_amt     numeric(14, 2) NOT NULL,
    txn_type_cd  varchar(4),
    reversal_flg char(1) NOT NULL DEFAULT 'N',
    posted_flg   char(1) NOT NULL DEFAULT 'Y'
);

CREATE INDEX emp_mst_org ON erp.emp_mst (org_cd);
CREATE INDEX emp_comp_hist_curr ON erp.emp_comp_hist (emp_no, curr_flg);
CREATE INDEX prj_hdr_org ON erp.prj_hdr (own_org_cd);
CREATE INDEX ar_inv_hdr_prj ON erp.ar_inv_hdr (prj_no);
CREATE INDEX gl_cost_txn_prj ON erp.gl_cost_txn (prj_no);

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

INSERT INTO erp.code_lkp (domain_cd, value_cd, value_desc, active_flg) VALUES
    ('EMP_STAT', 'A', 'Active', 'Y'),
    ('EMP_STAT', 'L', 'On Leave', 'Y'),
    ('EMP_STAT', 'I', 'Inactive', 'Y'),
    ('EMP_STAT', 'T', 'Terminated', 'Y'),
    ('PRJ_STAT', 'OPN', 'Open', 'Y'),
    ('PRJ_STAT', 'CLS', 'Closed', 'Y'),
    ('PRJ_STAT', 'HLD', 'On Hold', 'Y'),
    ('INV_STAT', 'ISS', 'Issued', 'Y'),
    ('INV_STAT', 'PD', 'Paid', 'Y'),
    ('INV_STAT', 'VOI', 'Void', 'Y'),
    ('TXN_TYPE', 'LAB', 'Labour', 'Y'),
    ('TXN_TYPE', 'MAT', 'Materials', 'Y'),
    ('TXN_TYPE', 'SUB', 'Subcontract', 'Y'),
    ('TXN_TYPE', 'EXP', 'Expenses', 'Y');

-- Two units share the display name "Operations" in different regions, which is
-- how the original merger left them.
INSERT INTO erp.org_unit_lkp (org_cd, org_nm, region_cd, parent_org_cd, active_flg) VALUES
    ('OU1000', 'Platform Engineering', 'NA',  NULL,     'Y'),
    ('OU1100', 'Data Engineering',     'NA',  'OU1000', 'Y'),
    ('OU2000', 'Commercial',           'NA',  NULL,     'Y'),
    ('OU2100', 'Operations',           'NA',  'OU2000', 'Y'),
    ('OU2200', 'Operations',           'EMEA','OU2000', 'Y'),
    ('OU3000', 'Finance',              'NA',  NULL,     'Y'),
    ('OU3100', 'People',               'NA',  NULL,     'Y'),
    ('OU4000', 'Professional Services','EMEA',NULL,     'Y'),
    ('OU4100', 'Field Delivery',       'EMEA','OU4000', 'Y'),
    ('OU9000', 'Legacy Hardware',      'NA',  NULL,     'N');

INSERT INTO erp.cust_mst (cust_cd, cust_nm, region_cd, active_flg) VALUES
    ('C0001', 'ACME Holdings',            'NA',  'Y'),
    ('C0002', 'ACME Holding Co.',         'EMEA','Y'),
    ('C0003', 'Northwind Traders',        'NA',  'Y'),
    ('C0004', 'Northwind Trading Ltd',    'EMEA','Y'),
    ('C0005', 'Globex Corporation',       'NA',  'Y'),
    ('C0006', 'Initech Systems',          'NA',  'Y'),
    ('C0007', 'Umbrella Health',          'EMEA','Y'),
    ('C0008', 'Soylent Foods',            'NA',  'Y'),
    ('C0009', 'Stark Industrial',         'NA',  'Y'),
    ('C0010', 'Wayne Logistics',          'EMEA','Y'),
    ('C0011', 'Tyrell Analytics',         'NA',  'Y'),
    ('C0012', 'Cyberdyne Robotics',       'NA',  'N'),
    ('C0013', 'Vandelay Imports',         'EMEA','Y'),
    ('C0014', 'Gringotts Financial',      'EMEA','Y'),
    ('C0015', 'Duff Beverages',           'NA',  'Y'),
    ('C0016', 'Bluth Property',           'NA',  'Y'),
    ('C0017', 'Prestige Worldwide',       'EMEA','Y'),
    ('C0018', 'Hooli Cloud',              'NA',  'Y'),
    ('C0019', 'Pied Piper Data',          'NA',  'Y'),
    ('C0020', 'Massive Dynamic',          'EMEA','Y'),
    ('C0021', 'Oceanic Freight',          'EMEA','Y'),
    ('C0022', 'Virtucon Capital',         'NA',  'Y');
