import pyodbc
conn = pyodbc.connect('Driver={ODBC Driver 18 for SQL Server};Server=NRFVMSSQL04;Database=NRF_REPORTS;Trusted_Connection=Yes;Encrypt=no;')
cursor = conn.cursor()
cursor.execute("""
    SELECT TOP 5
        CAST(o.[ACCOUNT#I] AS NVARCHAR(50)) AS account_number,
        CAST(o.INVOICE_DATE_YYYYMMDD AS BIGINT) AS invoice_date_raw,
        SUM(o.ENTENDED_PRICE_NO_FUNDS) AS total_sales
    FROM dbo._ORDERS o
    WHERE o.[INVOICE#] > 0
      AND o.COST_CENTER LIKE '0%'
      AND CAST(o.[ACCOUNT#I] AS NVARCHAR) NOT IN ('1')
      AND o.INVOICE_DATE_YYYYMMDD > 0
      AND CAST(o.[ACCOUNT#I] AS NVARCHAR) IN ('50039')
    GROUP BY o.[ACCOUNT#I], o.INVOICE_DATE_YYYYMMDD
""")
rows = cursor.fetchall()
print(f"Rows returned: {len(rows)}")
for r in rows[:5]:
    print(r)
cols = [r[0] for r in cursor.fetchall()]
price_cols = [c for c in cols if any(k in c.upper() for k in ['PRICE','AMOUNT','AMT','EXT','SALES','FUND','TOTAL','NET'])]
print('Price-related columns:')
for c in price_cols:
    print(' ', c)
print()
print('All columns:')
for c in cols:
    print(' ', c)
