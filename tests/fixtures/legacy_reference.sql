-- Canonical reference queries for the legacy ERP fixture.
--
-- Hand-written from the documented business rules, deliberately independent of
-- anything the application generates. These are the yardstick: when a model
-- writes different SQL, this decides which one is wrong.
--
-- The rules encoded here, and the trap each one avoids:
--
--   Active headcount        emp_mst.stat_cd = 'A'
--                           (terminated and on-leave staff are not active)
--   Current annual payroll  emp_comp_hist rows with curr_flg = 'Y'
--                           (never the sum of compensation history)
--   Average current salary  mean of those same current rows
--                           (an employee with no current row is excluded)
--   Invoiced amount         sum of qty * unit_amt - disc_amt over lines of
--                           headers where void_flg = 'N'
--   Project cost            sum of cost_amt where posted_flg = 'Y'
--                           and reversal_flg = 'N'
--   Project margin          invoiced amount - project cost
--
-- Each statement is named by the marker line above it so the harness can pick
-- one out without depending on statement order.

-- name: active_headcount
SELECT count(*) AS active_headcount
  FROM erp.emp_mst
 WHERE stat_cd = 'A';

-- name: current_annual_payroll
SELECT COALESCE(sum(ann_sal_amt), 0) AS current_annual_payroll
  FROM erp.emp_comp_hist
 WHERE curr_flg = 'Y';

-- name: average_current_salary
SELECT round(avg(ann_sal_amt), 2) AS average_current_salary
  FROM erp.emp_comp_hist
 WHERE curr_flg = 'Y';

-- name: employees_without_current_compensation
SELECT count(*) AS employees_without_current_compensation
  FROM erp.emp_mst AS e
 WHERE NOT EXISTS (
           SELECT 1
             FROM erp.emp_comp_hist AS c
            WHERE c.emp_no = e.emp_no
              AND c.curr_flg = 'Y'
       );

-- name: payroll_by_org
SELECT o.org_cd,
       o.org_nm,
       COALESCE(sum(c.ann_sal_amt), 0) AS current_annual_payroll,
       count(DISTINCT e.emp_no) FILTER (WHERE e.stat_cd = 'A')
           AS active_headcount
  FROM erp.org_unit_lkp AS o
  LEFT JOIN erp.emp_mst AS e ON e.org_cd = o.org_cd
  LEFT JOIN erp.emp_comp_hist AS c
         ON c.emp_no = e.emp_no AND c.curr_flg = 'Y'
 GROUP BY o.org_cd, o.org_nm
 ORDER BY o.org_cd;

-- name: invoiced_by_project
SELECT p.prj_no,
       COALESCE(sum(l.qty * l.unit_amt - l.disc_amt), 0) AS invoiced_amount
  FROM erp.prj_hdr AS p
  LEFT JOIN erp.ar_inv_hdr AS h
         ON h.prj_no = p.prj_no AND h.void_flg = 'N'
  LEFT JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no
 GROUP BY p.prj_no
 ORDER BY p.prj_no;

-- name: cost_by_project
SELECT p.prj_no,
       COALESCE(sum(t.cost_amt), 0) AS project_cost
  FROM erp.prj_hdr AS p
  LEFT JOIN erp.gl_cost_txn AS t
         ON t.prj_no = p.prj_no
        AND t.posted_flg = 'Y'
        AND t.reversal_flg = 'N'
 GROUP BY p.prj_no
 ORDER BY p.prj_no;

-- name: margin_by_project
WITH billed AS (
    SELECT h.prj_no,
           sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced_amount
      FROM erp.ar_inv_hdr AS h
      JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no
     WHERE h.void_flg = 'N'
     GROUP BY h.prj_no
),
spent AS (
    SELECT t.prj_no, sum(t.cost_amt) AS project_cost
      FROM erp.gl_cost_txn AS t
     WHERE t.posted_flg = 'Y' AND t.reversal_flg = 'N'
     GROUP BY t.prj_no
)
SELECT p.prj_no,
       COALESCE(b.invoiced_amount, 0) AS invoiced_amount,
       COALESCE(s.project_cost, 0) AS project_cost,
       COALESCE(b.invoiced_amount, 0) - COALESCE(s.project_cost, 0)
           AS project_margin
  FROM erp.prj_hdr AS p
  LEFT JOIN billed AS b ON b.prj_no = p.prj_no
  LEFT JOIN spent AS s ON s.prj_no = p.prj_no
 ORDER BY p.prj_no;

-- name: margin_by_org
-- Each fact is reduced to project grain before it meets the other, which is
-- what stops the header/detail join multiplying the cost side.
WITH billed AS (
    SELECT h.prj_no,
           sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced_amount
      FROM erp.ar_inv_hdr AS h
      JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no
     WHERE h.void_flg = 'N'
     GROUP BY h.prj_no
),
spent AS (
    SELECT t.prj_no, sum(t.cost_amt) AS project_cost
      FROM erp.gl_cost_txn AS t
     WHERE t.posted_flg = 'Y' AND t.reversal_flg = 'N'
     GROUP BY t.prj_no
)
SELECT o.org_cd,
       o.org_nm,
       COALESCE(sum(b.invoiced_amount), 0) AS invoiced_amount,
       COALESCE(sum(s.project_cost), 0) AS project_cost,
       COALESCE(sum(b.invoiced_amount), 0) - COALESCE(sum(s.project_cost), 0)
           AS project_margin
  FROM erp.org_unit_lkp AS o
  LEFT JOIN erp.prj_hdr AS p ON p.own_org_cd = o.org_cd
  LEFT JOIN billed AS b ON b.prj_no = p.prj_no
  LEFT JOIN spent AS s ON s.prj_no = p.prj_no
 GROUP BY o.org_cd, o.org_nm
 ORDER BY o.org_cd;

-- name: payroll_per_active_employee_by_org
SELECT o.org_cd,
       o.org_nm,
       count(DISTINCT e.emp_no) FILTER (WHERE e.stat_cd = 'A')
           AS active_headcount,
       COALESCE(sum(c.ann_sal_amt), 0) AS current_annual_payroll,
       CASE
           WHEN count(DISTINCT e.emp_no) FILTER (WHERE e.stat_cd = 'A') = 0
           THEN NULL
           ELSE round(
                    COALESCE(sum(c.ann_sal_amt), 0)
                    / count(DISTINCT e.emp_no) FILTER (WHERE e.stat_cd = 'A'),
                    2
                )
       END AS payroll_per_active_employee
  FROM erp.org_unit_lkp AS o
  LEFT JOIN erp.emp_mst AS e ON e.org_cd = o.org_cd
  LEFT JOIN erp.emp_comp_hist AS c
         ON c.emp_no = e.emp_no AND c.curr_flg = 'Y'
 GROUP BY o.org_cd, o.org_nm
 ORDER BY o.org_cd;

-- name: invoiced_by_customer
WITH billed AS (
    SELECT h.prj_no,
           sum(l.qty * l.unit_amt - l.disc_amt) AS invoiced_amount
      FROM erp.ar_inv_hdr AS h
      JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no
     WHERE h.void_flg = 'N'
     GROUP BY h.prj_no
)
SELECT c.cust_cd,
       c.cust_nm,
       COALESCE(sum(b.invoiced_amount), 0) AS invoiced_amount
  FROM erp.cust_mst AS c
  LEFT JOIN erp.prj_hdr AS p ON p.cust_cd = c.cust_cd
  LEFT JOIN billed AS b ON b.prj_no = p.prj_no
 GROUP BY c.cust_cd, c.cust_nm
 ORDER BY invoiced_amount DESC, c.cust_cd;

-- name: employees_with_multiple_compensation_changes
SELECT count(*) AS employees_with_multiple_changes
  FROM (
        SELECT c.emp_no
          FROM erp.emp_comp_hist AS c
          JOIN erp.emp_mst AS e ON e.emp_no = c.emp_no
         WHERE e.stat_cd = 'A'
         GROUP BY c.emp_no
        HAVING count(*) > 1
       ) AS changed;

-- name: projects_invoiced_without_posted_costs
SELECT count(*) AS projects_invoiced_without_costs
  FROM erp.prj_hdr AS p
 WHERE EXISTS (
           SELECT 1
             FROM erp.ar_inv_hdr AS h
             JOIN erp.ar_inv_ln AS l ON l.inv_no = h.inv_no
            WHERE h.prj_no = p.prj_no AND h.void_flg = 'N'
       )
   AND NOT EXISTS (
           SELECT 1
             FROM erp.gl_cost_txn AS t
            WHERE t.prj_no = p.prj_no
              AND t.posted_flg = 'Y'
              AND t.reversal_flg = 'N'
       );
