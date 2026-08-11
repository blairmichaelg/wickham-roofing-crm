# Wickham Roofing — Accounting Guide

**Wickham Roofing CRM v4 · Accounting Role**

Welcome to the Wickham Roofing Accounting system! This guide explains how to use your Accounting Ledger. Your main tasks here are tracking checks (both ACV and Supplement) with real dollar amounts, managing canvasser commissions, exporting invoices to QuickBooks Online, and reviewing job details.

---

## 1. Logging In

1. Open the app and enter your assigned 4-digit PIN.
2. Once logged in, you will be taken directly to the **"Accounting Ledger."** You'll see the Wickham Roofing logo at the top.

To log out, click **Logout** in the top-right corner of your dashboard.

**Note:** As the accountant, you have access to financial data across all jobs and all sales reps. You can see everyone's commission data because aggregating and paying commissions is your core responsibility. Please keep your PIN secure.

---

## 2. Understanding the Ledger

At the top of your dashboard, you'll see two quick-glance metric cards:

- **"Supplemented RCV Added"** — The total additional value your team has won through approved supplements.
- **"QuickBooks Export Queue"** — The number of jobs that are finished and waiting to be exported to QuickBooks Online.

Below the metrics, you'll find your working tables: one for tracking ACV/Supplement checks, one for commissions that are ready to pay, and the QuickBooks export tool.

If a table is empty (for example, if there are no pending checks today), you'll see a friendly message like **"No pending checks to record"** so you always know you're fully caught up.

### Viewing Full Job Details
Every job row includes a **"View Details →"** link. Clicking this opens the full job profile. This gives you complete access to everything about that job — including financials, profit margins, measurement data, signed documents, and supplements. 

---

## 3. Recording Checks (ACV & Supplement)

To protect the company against insurance carriers short-paying us, the system requires you to enter the **actual dollar amount and date** for every check received, rather than just clicking a simple "received" checkbox.

### How to record a check
1. Find the job under the ACV or Supplement section that is currently marked **"Pending."**
2. Click it to open the check entry form.
3. Enter two things:
   - **Amount:** The actual dollar figure written on the check.
   - **Date received:** The date you received it.
4. Click **"Confirm Received."**

### The Short-Pay Warning
The system automatically calculates what the check *should* be based on the carrier's approved numbers:
- **Expected ACV Check:** The total Carrier Replacement Cost Value (RCV) minus the Recoverable Depreciation (the portion the carrier holds back until the job is done).
- **Expected Supplement Check:** The Recoverable Depreciation amount itself.

If the check amount you enter is lower than the expected value by about 2% or more, the system will pause and warn you:

> *"This amount ($X) is less than the expected $Y. This may indicate a carrier short-pay. Continue anyway?"*

This is not an error! It is simply a warning to double-check your typing. If the carrier genuinely shorted us on the payment, confirm the amount anyway so the system has the correct record, and follow up with the insurance carrier separately to collect the missing funds.

---

## 4. Paying Commissions

### The Default Commission Rate
Every job automatically defaults to paying the canvasser a commission of **10% of total revenue** (the full roof sale price). This is not based on profit, and permit fees are not deducted from this calculation.

### Adjusting a Commission Manually
If you have a special arrangement with a rep for a specific job, you can manually override their commission percentage:

1. Find the job's commission amount and click **"Adjust %."**
2. An input box will appear, pre-filled with the current rate (10%).
3. Enter the new percentage (for example, `12` or `15`).
4. Click **"Save."**
5. You will be asked to confirm the change.
6. Once confirmed, the commission amount recalculates instantly!

### Resetting a Job Back to the Default
1. Click **"Reset"** next to the job.
2. Confirm the prompt to reset the job back to 10%.
3. The commission immediately recalculates back to the standard rate.

**Important:** Overrides apply *only* to the specific job you edit. Every other job continues to automatically use the standard 10% default unless you adjust it.

### Downloading Commission Documents
When you are ready to pay a rep, click **"Download PDF"** next to their commission entry. The system will instantly generate a beautifully formatted commission statement document for your payroll records.

---

## 5. Exporting to QuickBooks Online

1. Click **"Export QBO CSV."**
2. The button will change to **"Exporting..."** while the file is prepared.
3. A spreadsheet file (CSV) will download automatically to your computer. It contains one row for every job ready to be invoiced, pre-formatted with:
   - Customer name
   - Invoice date (today)
   - Due date (Net 30)
   - Terms (Net 30)
   - Item description ("Roofing Services")
   - Amount (based on carrier RCV)
   - Memo (invoice ID and claim number)

You can upload this file directly into QuickBooks Online to generate your invoices in bulk!

### Duplicate Export Protection
Once a job is exported, the system permanently marks it as exported. It will **never** appear in future export batches, so you don't have to worry about accidentally double-billing a customer in QuickBooks.

---

## 5b. Financial Data Integrity & Verification

To protect Wickham Roofing against inaccurate accounting data, the system runs strict, automated math checks on all carrier-provided Statement of Loss inputs:
- **Automatic Math Gates**: The system prevents any incorrect numbers from being saved. If a line item's RCV minus depreciation doesn't equal its ACV, or if the overall claim financials (gross_rcv - depreciation - deductible == net_claim) don't balance within 5 cents, the system will raise an error and block the data from being imported.
- **Pure Data Ingestion**: The artificial intelligence engine only copies text from documents and is strictly forbidden from calculating or guessing any numbers, ensuring that all math remains 100% deterministic and verified on the Python server side.

---

## 6. System Permissions & Boundaries

To keep the system organized, certain tools are restricted based on your role.

**Things you cannot do in the system:**
- You cannot access the Admin control panel or the Triage tools.
- You cannot access Field rep intake screens or Operations' crew scheduling tools.
- You cannot create or manage employee user accounts.

**Things you CAN do:**
- Via the "View Details →" link on your ledger, you have full access to any job's complete file.
- You have access to every sales rep's commission data to process payroll. 

---

## Frequently Asked Questions (FAQ)

**Q: Why does the commission calculate from revenue instead of profit?**
By design, Wickham Roofing pays canvassers 10% of the total roof sale price, not a share of profit. This applies to every job automatically unless you manually adjust it.

**Q: I need to pay a rep a different percentage on one specific job. Will that change everyone else's rate?**
No. When you use the **"Adjust %"** button on a specific job, it only affects that single job. Every other job remains at the default 10%.

**Q: Why is my ACV check amount supposed to be less than the total carrier RCV?**
That is completely normal. Carriers typically withhold recoverable depreciation until the job is finished, so the ACV check is intentionally lower than the total RCV. The system calculates the *correct* expected ACV (RCV minus depreciation), so you will only see a warning if the check is actually short.

**Q: Can I export the same job to QuickBooks twice by accident?**
No. Once a job is exported, it is automatically removed from future export batches.

**Q: Can I see a job's full financials and signed documents?**
Yes! Click the **"View Details →"** link on any job row. You will see the full job profile with all financials, margins, documents, and supplements.

---

*This guide reflects the production features for version 2.1.0 of the Wickham Roofing CRM.*
