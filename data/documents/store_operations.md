# Retail Store Operations & Inventory Governance

**Track ID:** PS03  
**Target Audience:** Store Managers, Inventory Controllers, Regional Operations  
**Currency Standard:** Indian Rupee (INR - ₹)  
**Version:** 1.0

---

## 1. Operating Assumptions
- **Store Hours:** Operating hours are 09:00 to 22:00 IST, 7 days a week.
- **End-of-Day Reconciliation:** All register closures, daily transactions, damaged item entries, and goods receipts must be reconciled and locked by 23:00 IST each night.
- **Ledger Invariance:** Closing inventory from Day $T$ becomes the non-negotiable opening inventory for Day $T+1$.

---

## 2. Inter-Store Transfer Policy
When localized demand imbalances occur (e.g., Stockout Risk in Store A vs. Overstock in Store B), inter-store transfers minimize lost sales and avoid supplier MOQs.

### Conditions for Transfer Authorization:
1. **Donor Store Protection:** The donor store must retain at least $1.5 \times \text{Safety Stock}$ after transferring the designated units.
2. **Transit Feasibility:** Stores within the same metropolitan or regional cluster can transfer inventory within 24 to 48 hours.
3. **Margin Preservation:** The transfer logistics fee must not exceed 15% of the gross profit margin of transferred goods.
4. **Authorization:** Requires digital handshake by both dispatching and receiving store managers.

---

## 3. Damaged & Expired Inventory
- **Immediate Quarantine:** Any unit with packaging compromise, leak, seal break, or past expiry date must be instantly removed from the sales floor.
- **System Logging:** Must be recorded in the daily inventory ledger under `damaged_quantity` with photo verification and reason code (Transit Damaged, In-Store Customer Handling, Rodent/Pest, Expiry).
- **Vendor Credit / RTV:** If damage is identified at delivery dock during PO unloading, the quantity is rejected at receipt (`received_quantity < ordered_quantity`) and credit note requested.

---

## 4. Stock Counting & Audits
- **Perpetual Cycle Counting:**
  - High-velocity / high-value SKUs (Category A): Audited weekly.
  - Category B SKUs: Audited bi-weekly.
  - Category C & slow-moving SKUs: Audited monthly.
- **Discrepancy Treatment:**
  - Audit count variance vs. book stock must be recorded as `adjustment_quantity` (positive if excess found, negative if shrinkage/theft detected).
  - Shrinkage $> ₹1,000$ on a single SKU requires formal loss prevention inquiry.

---

## 5. Escalation Rules
- **P1 Escalation (Critical):**
  - Out of stock on top-10 revenue generating SKUs for $> 24 \text{ hours}$.
  - PO delivery delayed by $> 48 \text{ hours}$ past `expected_date` for essential staple goods.
  - *Response Time:* Store Manager and Category Buyer must review within 4 hours.
- **P2 Escalation (Major):**
  - Overstock exceeding ₹50,000 idle capital for $> 45 \text{ days}$.
  - System inventory variance discrepancy $> 2\%$ of total store inventory value.
  - *Response Time:* Operational resolution within 48 hours.
- **P3 Escalation (Standard):**
  - Minor supplier fill rate variance ($< 5\%$ short delivery).
  - Routine markdown clearance for nearing-expiry goods.
