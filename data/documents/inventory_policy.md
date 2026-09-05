# Retail Inventory Policy

**Track ID:** PS03  
**Applicability:** All Retail Store Outlets, Distribution Centers, and Store Managers  
**Currency Standard:** Indian Rupee (INR - ₹)  
**Version:** 1.0

---

## 1. Purpose & Core Principles
This policy sets the operational definitions and computational thresholds for inventory health across all retail locations. Every store operates on a deterministic inventory monitoring standard to protect revenue, prevent capital lockup, and ensure high customer fulfillment.

Key Principles:
1. **FIFO (First-In, First-Out):** Mandatory for all perishables, grocery, beverages, and personal care lines.
2. **Stock Availability vs. Real Demand:** Zero sales during a stockout period must never be recorded as zero demand; it represents lost demand due to stock unreadiness.
3. **Data Integrity:** Ledger balance equation must strictly balance daily:
   $$\text{Closing Stock} = \text{Opening Stock} + \text{Received} - \text{Sold} + \text{Returned} - \text{Damaged} \pm \text{Adjustments}$$

---

## 2. Stockout Risk Classification
Stockout risk is computed dynamically from **Days of Supply (DoS)** and **Average Daily Sales (ADS)**:

$$\text{ADS} = \frac{\sum_{t=1}^{N} \text{Units Sold on Active Days}}{N} \quad (N = 14 \text{ to } 30 \text{ days})$$
$$\text{Days of Supply (DoS)} = \frac{\text{Current Stock on Hand}}{\text{ADS}}$$

### Risk Thresholds:
- **Critical Stockout Risk:** $\text{DoS} < \text{Supplier Lead Time} \text{ or } \text{DoS} \le 3.0 \text{ days}$.
  - *Action:* Immediate trigger of purchase order or urgent inter-store stock balancing.
- **Elevated Stockout Risk:** $\text{DoS} \le \text{Reorder Point (ROP)}$ or $\text{DoS} \in (3.0, 7.0] \text{ days}$.
  - *Action:* Flag on the morning manager copilot dashboard.
- **Healthy Runway:** $\text{DoS} \in (7.0, 45.0] \text{ days}$.
  - *Action:* Standard replenishment cycles apply.

---

## 3. Slow-Moving & Stagnant Inventory
Capital tied up in non-moving stock decreases store ROI and increases shrinkage.

### Definitions:
- **Slow-Moving Inventory (SMI):**
  - An active SKU whose inventory turnover is $< 0.5$ turns per quarter ($\text{DoS} > 60 \text{ days}$ without a planned seasonal buffer).
  - Products with sales velocity falling below $0.15 \text{ units/day}$ across 30 consecutive operating days.
- **Dead / Stagnant Stock:**
  - Zero units sold over 30 consecutive operating days while continuous stock on hand $> 0$.
  - *Action:* Automated trigger for promotional markdown, end-cap display placement, or return to vendor (RTV) subject to supplier agreements.

---

## 4. Overstock Definitions
Overstock occurs when inventory holding significantly exceeds foreseeable demand and safety buffers.

### Thresholds:
- **Standard Categories (Beverages, Snacks, Grocery, Home Care):**
  - Stock on Hand $> 3 \times \text{Monthly Demand Forecast}$ OR $\text{DoS} > 60 \text{ days}$.
- **Longer Shelf-Life Categories (Apparel, Electronics Accessories, Stationery):**
  - $\text{DoS} > 90 \text{ days}$ with capital allocation exceeding ₹15,000 per SKU.
- **Consequences:**
  - Storage space saturation, risk of packaging damage, expiry write-offs, and working capital opportunity cost.
  - Recommended action: Halt incoming purchase orders, transfer excess units to high-velocity store branches.
