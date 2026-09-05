# Retail Replenishment Policy

**Track ID:** PS03  
**Applicability:** Procurement, Supply Chain Managers, Store Managers  
**Currency Standard:** Indian Rupee (INR - ₹)  
**Version:** 1.0

---

## 1. Replenishment Fundamentals
Efficient replenishment balances product availability on store shelves against holding costs and order transaction costs.

---

## 2. Reorder Point (ROP) Framework
The Reorder Point determines the precise inventory level that automatically triggers a new purchase order.

$$\text{ROP} = (\text{Average Daily Demand} \times \text{Supplier Lead Time in Days}) + \text{Safety Stock}$$

### Parameters:
- **Average Daily Demand (ADD):** Rolling 14-day median or trimmed mean daily sales during in-stock days.
- **Supplier Lead Time (SLT):** The elapsed calendar days from PO confirmation to physical delivery and shelf induction at the designated store.
- **Safety Stock (SS):**
  $$\text{Safety Stock} = Z \times \sigma_{\text{demand}} \times \sqrt{\text{Lead Time}}$$
  - For standard fast-moving retail SKUs, a minimum buffer of $3 \times \text{ADD}$ (or $7 \text{ days}$ for long-lead suppliers) is enforced.

---

## 3. Order Quantity & Constraints
- **Economic & Practical Order Quantity:**
  Orders must satisfy the supplier's **Minimum Order Quantity (MOQ)** while capping holding stock at optimal target levels:
  $$\text{Order Quantity} = \max(\text{MOQ}, \text{Target Stock Level} - \text{Current Stock} - \text{On Order})$$
  $$\text{Target Stock Level} = (\text{Review Period Days} + \text{Lead Time}) \times \text{ADD} + \text{Safety Stock}$$
- **Multiples & Packaging:** All orders round up to master carton / pack sizes specified in the vendor agreement.

---

## 4. Supplier Lead Time Classifications
1. **Local Direct Store Delivery (DSD):** 2 to 4 business days (e.g., fresh snacks, local daily dairy/beverages).
2. **Regional Central Distribution:** 5 to 7 business days (standard FMCG, personal care, dry groceries).
3. **National / Long-Distance Sourcing:** 14 to 25 business days (specialized electronics accessories, apparel lines, imported stationery).

*Notice on Long Lead-Time Vendors:* Requires automated early-warning alerts when stock drops below 1.5x ROP.

---

## 5. Store Manager Approval Matrix
Automated procurement engines generate proposed Purchase Orders (POs) into draft status.

| Order Value Threshold (₹) | Lead Time Window | Approval Level Required |
| :--- | :--- | :--- |
| $\le ₹15,000$ | Standard ($\le 7$ days) | Assistant Store Manager / System Auto-Dispatch |
| $₹15,001 - ₹50,000$ | Standard ($\le 7$ days) | Store Manager |
| $> ₹50,000$ | Any Lead Time | Store Manager + Regional Ops Head |
| Any Value | Extended Lead Time ($> 14$ days) | Store Manager Mandatory Review |
| Expedited / Emergency PO | Any | Store Manager Approval with Justification Log |
