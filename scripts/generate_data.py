"""
Deterministic Synthetic Retail Data Generator
TRACK_ID: PS03 (Retail - Sales and Inventory Copilot)

Generates realistic, internally consistent retail data for Indian retail context (INR).
Embedded scenarios:
1. Fast-moving items approaching stockout (DoS < 2 days).
2. Overstocked items (DoS > 120-180 days).
3. Slow-moving / stagnant items.
4. Sales spike scenario (seasonal monsoon surge).
5. Sales drop scenario (sharp decline mid-period).
6. Store-specific performance divergence (Tech Hub Cyberabad vs residential).
7. Long lead-time supplier (25 days).
8. Delayed purchase orders.
9. Zero sales for extended period despite stock (dead stock).
10. Zero sales caused by stockout (lost sales vs demand disappearance).
"""

import os
import csv
import random
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def generate_retail_dataset(seed: int = 42):
    random.seed(seed)
    os.makedirs(DATA_DIR, exist_ok=True)

    # 1. STORES
    stores = [
        {"store_id": "STR-001", "store_name": "Nexus Koramangala", "city": "Bengaluru", "state": "Karnataka", "store_type": "Flagship Supermarket"},
        {"store_id": "STR-002", "store_name": "Phoenix Palladium", "city": "Mumbai", "state": "Maharashtra", "store_type": "Urban Express"},
        {"store_id": "STR-003", "store_name": "Select Citywalk", "city": "New Delhi", "state": "Delhi", "store_type": "Metro Hypermarket"},
        {"store_id": "STR-004", "store_name": "Inorbit Cyberabad", "city": "Hyderabad", "state": "Telangana", "store_type": "Tech Hub Mart"},
    ]

    # 2. SUPPLIERS
    suppliers = [
        {"supplier_id": "SUP-001", "supplier_name": "Nilgiri Beverages Ltd", "lead_time_days": 4, "minimum_order_quantity": 40},
        {"supplier_id": "SUP-002", "supplier_name": "Haldiram Snacks Logistics", "lead_time_days": 3, "minimum_order_quantity": 50},
        {"supplier_id": "SUP-003", "supplier_name": "Desi Agro Staples Corp", "lead_time_days": 6, "minimum_order_quantity": 30},
        {"supplier_id": "SUP-004", "supplier_name": "Patanjali Organic Supplies", "lead_time_days": 5, "minimum_order_quantity": 45},
        {"supplier_id": "SUP-005", "supplier_name": "Himalaya Wellness & Care", "lead_time_days": 5, "minimum_order_quantity": 25},
        {"supplier_id": "SUP-006", "supplier_name": "Godrej Home Essentials", "lead_time_days": 4, "minimum_order_quantity": 35},
        {"supplier_id": "SUP-007", "supplier_name": "Dabur Consumer Goods", "lead_time_days": 5, "minimum_order_quantity": 40},
        {"supplier_id": "SUP-008", "supplier_name": "ITC Foods & Staples", "lead_time_days": 4, "minimum_order_quantity": 60},
        {"supplier_id": "SUP-009", "supplier_name": "FabIndia Handlooms & Textiles", "lead_time_days": 12, "minimum_order_quantity": 20},
        {"supplier_id": "SUP-010", "supplier_name": "Camlin Stationery Works", "lead_time_days": 5, "minimum_order_quantity": 50},
        {"supplier_id": "SUP-011", "supplier_name": "Apex Electronics & Gadgets", "lead_time_days": 25, "minimum_order_quantity": 100}, # LONG LEAD TIME
        {"supplier_id": "SUP-012", "supplier_name": "V-Guard Electrical Accessories", "lead_time_days": 8, "minimum_order_quantity": 30},
    ]

    # 3. PRODUCTS (108 items)
    categories_spec = [
        ("Beverages", [
            ("Premium Masala Chai 250g", "Tea & Coffee", "Tata Tea", "SUP-001", 110.0, 150.0, 30, 365),
            ("South Indian Filter Coffee 200g", "Tea & Coffee", "Bru Gold", "SUP-001", 130.0, 185.0, 25, 270),
            ("Electrolyte Hydration Drink 500ml", "Energy & Sports", "Enerzal", "SUP-001", 35.0, 50.0, 40, 180), # Sales spike
            ("Cold Pressed Alphonso Mango Juice 500ml", "Juices", "Raw Pressery", "SUP-001", 70.0, 110.0, 50, 45), # Fast moving stockout
            ("Pure Coconut Water 200ml", "Juices", "Paper Boat", "SUP-001", 30.0, 45.0, 35, 120),
            ("Sparkling Jeera Soda 300ml", "Carbonated", "Lahori", "SUP-001", 15.0, 25.0, 40, 180),
            ("Organic Green Tea Lemon 25 bags", "Tea & Coffee", "Organic India", "SUP-001", 160.0, 240.0, 20, 540),
            ("Badam Milk Drink 200ml", "Dairy Drinks", "Amul", "SUP-001", 28.0, 40.0, 30, 90),
            ("Apple Cider Vinegar 500ml", "Health Drinks", "Kapiva", "SUP-005", 220.0, 350.0, 15, 730),
            ("Premium Darjeeling First Flush 100g", "Tea & Coffee", "Goodwyn", "SUP-001", 380.0, 550.0, 10, 540),
            ("Amla Aloe Vera Juice 1L", "Health Drinks", "Baidyanath", "SUP-005", 140.0, 210.0, 15, 365),
            ("Artisanal Dark Chocolate Mocha Drink", "Specialty", "Smoor", "SUP-001", 120.0, 190.0, 15, 120),
            ("Instant Chicory Coffee Mix 100g", "Tea & Coffee", "Nescafe", "SUP-001", 85.0, 125.0, 25, 365),
            ("Kesar Pista Thandai Syrup 750ml", "Syrups", "Guruji", "SUP-001", 210.0, 320.0, 12, 365),
        ]),
        ("Snacks", [
            ("Roasted Makhana Himalayan Salt 80g", "Healthy Snacks", "Farmley", "SUP-002", 90.0, 140.0, 25, 180),
            ("Bikaneri Bhujia Sev 400g", "Namkeen", "Haldiram", "SUP-002", 85.0, 120.0, 40, 180),
            ("Masala Potato Wafers 120g", "Chips", "Balaji", "SUP-002", 28.0, 40.0, 50, 120),
            ("Multigrain Methi Khakhra 200g", "Traditional", "Jabsons", "SUP-002", 55.0, 80.0, 30, 150),
            ("Artisanal Dark Chocolate Bar 70% 80g", "Chocolates", "Amul Dark", "SUP-002", 65.0, 100.0, 30, 270), # Sales drop
            ("Salted California Almonds 200g", "Dry Fruits", "Nutraj", "SUP-002", 180.0, 260.0, 20, 365),
            ("Spicy Peri Peri Cashews 150g", "Dry Fruits", "Go Organic", "SUP-002", 210.0, 310.0, 15, 270),
            ("Digestive Fiber Biscuits 300g", "Biscuits", "NutriChoice", "SUP-008", 45.0, 65.0, 40, 180),
            ("Classic Butter Cookies 200g", "Bakery", "Unibic", "SUP-008", 50.0, 75.0, 30, 180),
            ("Roasted Chana Garlic Flavor 200g", "Healthy Snacks", "Haldiram", "SUP-002", 48.0, 70.0, 25, 180),
            ("Banana Chips Coconut Oil 150g", "Chips", "Kerala Pure", "SUP-002", 60.0, 90.0, 25, 90),
            ("Soan Papdi Ghee Box 500g", "Sweets", "Bikaji", "SUP-002", 140.0, 210.0, 15, 180),
            ("Baked Pita Chips Herb 150g", "Healthy Snacks", "Wingreens", "SUP-002", 85.0, 130.0, 20, 150),
            ("Cream & Onion Corn Puffs 80g", "Puffs", "Kurkure", "SUP-002", 22.0, 35.0, 45, 120),
        ]),
        ("Grocery", [
            ("Premium Basmati Rice 5kg", "Grains", "India Gate", "SUP-003", 420.0, 580.0, 20, 730),
            ("Unpolished Toor Dal 1kg", "Pulses", "Tata Sampann", "SUP-003", 130.0, 175.0, 35, 365),
            ("Cold Pressed Mustard Oil 1L", "Edible Oils", "Fortune", "SUP-003", 145.0, 195.0, 30, 365),
            ("Whole Wheat Chakki Atta 5kg", "Flours", "Aashirvaad", "SUP-008", 220.0, 290.0, 30, 180),
            ("Organic Desi Jaggery Powder 1kg", "Sweeteners", "Patanjali", "SUP-004", 80.0, 120.0, 25, 365),
            ("Tellicherry Whole Black Pepper 100g", "Spices", "Catch Spices", "SUP-003", 95.0, 145.0, 20, 540),
            ("Pure Ghee Cow Milk 500ml", "Dairy Staples", "Mother Dairy", "SUP-003", 285.0, 360.0, 25, 270),
            ("Moong Dal Yellow Split 1kg", "Pulses", "Organic Tattva", "SUP-003", 125.0, 170.0, 25, 365),
            ("Rock Salt Pink Himalayan 1kg", "Staples", "Tata Salt", "SUP-008", 60.0, 95.0, 30, 730),
            ("Kashmiri Red Chilli Powder 200g", "Spices", "MDH Spices", "SUP-003", 85.0, 125.0, 25, 365),
            ("Organic Quinoa Grain 500g", "Health Grains", "True Elements", "SUP-003", 160.0, 240.0, 15, 365),
            ("Refined Sunflower Oil Pouch 1L", "Edible Oils", "Sunpure", "SUP-003", 110.0, 145.0, 40, 365),
            ("Kabuli Chana Giant 1kg", "Pulses", "Rajdhani", "SUP-003", 140.0, 190.0, 20, 365),
            ("Organic Chia Seeds 250g", "Superfoods", "Nutty Gritties", "SUP-004", 135.0, 210.0, 15, 540),
        ]),
        ("Personal Care", [
            ("Purifying Neem Face Wash 150ml", "Skincare", "Himalaya", "SUP-005", 120.0, 175.0, 30, 730),
            ("Ayurvedic Anti-Hair Fall Shampoo 340ml", "Haircare", "Kesh King", "SUP-005", 195.0, 280.0, 25, 730),
            ("Mysore Sandalwood Soap 3x125g", "Bath & Body", "Mysore Sandal", "SUP-005", 180.0, 255.0, 25, 1095),
            ("Cold Pressed Virgin Coconut Hair Oil 200ml", "Haircare", "Parachute", "SUP-005", 95.0, 135.0, 35, 730),
            ("Herbal Ayurvedic Toothpaste 200g", "Oral Care", "Dabur Red", "SUP-007", 80.0, 115.0, 40, 730),
            ("Intensive Aloe Vera Body Lotion 400ml", "Skincare", "Vaseline", "SUP-005", 220.0, 325.0, 20, 730),
            ("Kumkumadi Radiance Face Serum 30ml", "Skincare", "Ayuga", "SUP-005", 420.0, 699.0, 12, 540),
            ("Natural Rose Water Spray 200ml", "Skincare", "Dabur Gulabari", "SUP-007", 65.0, 95.0, 25, 730),
            ("Refreshing Men Charcoal Deodorant 150ml", "Fragrances", "Wild Stone", "SUP-005", 135.0, 210.0, 25, 1095),
            ("Organic Bamboo Toothbrush 4-pack", "Oral Care", "Terrabrush", "SUP-005", 110.0, 180.0, 20, 1095),
            ("Herbal Sunscreen Lotion SPF 50 100ml", "Skincare", "Lotus Herbals", "SUP-005", 260.0, 395.0, 15, 730),
            ("Onion Black Seed Hair Oil 150ml", "Haircare", "WOW Skin Science", "SUP-005", 280.0, 449.0, 15, 730),
            ("Gentle Baby Bath Wash 400ml", "Baby Care", "Sebamed", "SUP-005", 410.0, 580.0, 10, 730),
            ("Hand Sanitizer Gel 500ml", "Hygiene", "Dettol", "SUP-005", 110.0, 160.0, 30, 730),
        ]),
        ("Home Care", [
            ("Citrus Pine Surface Disinfectant Floor Cleaner 1L", "Floor Care", "Lizol", "SUP-006", 130.0, 185.0, 35, 730),
            ("Lime & Neem Dishwash Gel Refill 750ml", "Kitchen Care", "Vim Gel", "SUP-006", 115.0, 160.0, 40, 730),
            ("Bio-Enzyme Laundry Detergent Powder 2kg", "Laundry", "Surf Excel", "SUP-006", 280.0, 380.0, 30, 730),
            ("Automatic Mosquito Vaporizer Refill Twin Pack", "Pest Control", "GoodKnight", "SUP-006", 110.0, 155.0, 35, 730),
            ("Lavender Room Air Freshener Spray 240ml", "Air Care", "Godrej aer", "SUP-006", 115.0, 169.0, 25, 730),
            ("Toilet Bowl Descaler Cleaner 1L", "Sanitary", "Harpic", "SUP-006", 125.0, 175.0, 35, 730),
            ("Microfiber Multipurpose Cleaning Cloths 4-pack", "Cleaning Tools", "Scotch-Brite", "SUP-006", 140.0, 220.0, 20, 1460),
            ("Fabric Conditioner Comfort Morning Fresh 860ml", "Laundry", "Comfort", "SUP-006", 155.0, 225.0, 25, 730),
            ("Steel Wool Scrub Pads Pack of 6", "Kitchen Care", "Exo", "SUP-006", 45.0, 70.0, 40, 1460),
            ("Drain Clog Remover Crystals 250g", "Sanitary", "Kiwi Dranex", "SUP-006", 65.0, 99.0, 15, 730),
            ("Herbal Camphor Cones Air Purifier 60g", "Air Care", "Mangalam", "SUP-006", 120.0, 180.0, 20, 365),
            ("Kitchen Degreaser Surface Spray 500ml", "Kitchen Care", "Colin", "SUP-006", 110.0, 165.0, 20, 730),
            ("Eco Bamboo Paper Towels 2 Rolls", "Paper Goods", "Beco", "SUP-006", 130.0, 199.0, 20, 1095),
        ]),
        ("Electronics Accessories", [
            ("Braided Fast Charging USB-C Cable 1.5m", "Cables", "Portronics", "SUP-011", 140.0, 299.0, 25, 1460), # Cyberabad hero item
            ("Dual Port 20W PD USB Wall Charger", "Chargers", "Ambrane", "SUP-011", 280.0, 599.0, 20, 1460), # Long lead time
            ("Wireless Bluetooth Earbuds IPX5", "Audio", "boAt", "SUP-011", 650.0, 1299.0, 20, 730),
            ("Heavy Duty 4-Way Surge Protector Spike Guard", "Power", "V-Guard", "SUP-012", 340.0, 550.0, 15, 1825),
            ("Ergonomic Aluminium Mobile Phone Desk Stand", "Stands", "ELV", "SUP-011", 160.0, 349.0, 15, 1825),
            ("Tempered Glass Screen Protector 9H 2-pack", "Protection", "Spigen", "SUP-011", 190.0, 499.0, 20, 1460),
            ("High Speed USB 3.0 Card Reader 4-in-1", "Data Transfer", "Quantum", "SUP-011", 120.0, 249.0, 15, 1460),
            ("Slim Magnetic Car Vent Phone Mount", "Automotive", "Gizga", "SUP-011", 140.0, 299.0, 15, 1825),
            ("Cat-6 High Speed Gigabit Ethernet Cable 3m", "Networking", "D-Link", "SUP-011", 110.0, 220.0, 15, 1825),
            ("Silicone Cable Organizer Clips 6-pack", "Cable Management", "Tizum", "SUP-011", 75.0, 160.0, 20, 1825),
            ("Waterproof Bluetooth Bicycle Speaker", "Audio", "Zebronics", "SUP-011", 520.0, 999.0, 12, 730),
            ("Rechargeable Wireless Silent Optical Mouse", "Peripherals", "Lenovo", "SUP-011", 380.0, 799.0, 15, 1095),
            ("Universal International Travel Adapter", "Power", "Syska", "SUP-012", 290.0, 599.0, 15, 1825),
        ]),
        ("Apparel", [
            ("Combed Cotton Men Solid Crew T-Shirt Navy M", "Men Tops", "FabIndia", "SUP-009", 320.0, 599.0, 15, 1825),
            ("Organic Handloom Linen Short Kurta Olive L", "Men Ethnic", "FabIndia", "SUP-009", 750.0, 1499.0, 10, 1825), # Overstocked item
            ("Bamboo Fiber Cushioned Ankle Socks 3-pack", "Innerwear", "Jockey", "SUP-009", 190.0, 349.0, 25, 1825),
            ("Pure Cotton Printed Jaipur Dupatta", "Women Ethnic", "FabIndia", "SUP-009", 280.0, 549.0, 12, 1825), # Slow moving
            ("Women Breathable Yoga Track Pants Black M", "Activewear", "Zivame", "SUP-009", 480.0, 899.0, 15, 1825),
            ("Men Formal Cotton Dress Socks 2-pack", "Innerwear", "Van Heusen", "SUP-009", 140.0, 260.0, 20, 1825),
            ("Pure Khadi Cotton Handkerchiefs 6-pack", "Accessories", "Khadi Naturals", "SUP-009", 120.0, 220.0, 20, 1825),
            ("Embroidered Cotton Tote Bag Ecru", "Accessories", "FabIndia", "SUP-009", 260.0, 499.0, 15, 1825),
            ("Microfiber Quick Dry Gym Towel 50x100cm", "Activewear", "Decathlon", "SUP-009", 160.0, 299.0, 15, 1825),
            ("Men Breathable Cotton Boxer Shorts Pack of 2", "Innerwear", "Jockey", "SUP-009", 280.0, 499.0, 20, 1825),
            ("Handloom Kalamkari Print Cotton Scarf", "Women Ethnic", "FabIndia", "SUP-009", 210.0, 399.0, 15, 1825),
            ("Cotton Canvas Daily Apron with Pockets", "Utility", "Home Center", "SUP-009", 180.0, 349.0, 12, 1825),
            ("Seamless Cotton Low-Cut Sports Socks 3-pack", "Innerwear", "Puma", "SUP-009", 220.0, 399.0, 20, 1825),
        ]),
        ("Stationery", [
            ("A5 Executive Hardbound Ruled Journal 192p", "Notebooks", "Classmate", "SUP-010", 140.0, 240.0, 25, 1825),
            ("Smooth Rollerball Liquid Gel Pens 3-pack", "Pens", "Camlin", "SUP-010", 85.0, 135.0, 40, 1095),
            ("Neon Repositionable Sticky Notes 3x3 4-pack", "Desk Notes", "Post-it", "SUP-010", 110.0, 175.0, 30, 1825),
            ("Dry Erase Whiteboard Marker Set 4 Colors", "Markers", "Faber-Castell", "SUP-010", 90.0, 140.0, 25, 730),
            ("Heavy Duty Metal Desktop Stapler No.10", "Desk Tools", "Kangaro", "SUP-010", 95.0, 150.0, 20, 3650), # Slow moving
            ("Artisan Calligraphy Fountain Pen & Nib Set", "Fine Writing", "Parker", "SUP-010", 450.0, 850.0, 10, 3650), # Dead stock
            ("Stainless Steel Craft Scissors 8-inch", "Desk Tools", "Kokuyo", "SUP-010", 80.0, 130.0, 20, 3650),
            ("Document Expanding File Folder 12 Pockets", "Filing", "Solo", "SUP-010", 160.0, 260.0, 15, 1825),
            ("Fluorescent Desk Highlighter Pen Set of 5", "Markers", "Stabilo", "SUP-010", 140.0, 220.0, 25, 730),
            ("Dual Core Correction Tape 12m", "Desk Tools", "Plus Japan", "SUP-010", 65.0, 110.0, 25, 1460),
            ("Retractable Mechanical Pencil 0.7mm with Leads", "Drafting", "Camlin", "SUP-010", 60.0, 95.0, 30, 1825),
            ("Desktop Rotating Pen & Stationery Stand Wood", "Organizers", "Cello", "SUP-010", 190.0, 320.0, 15, 3650),
            ("Plastic Ruler Shatterproof 30cm", "Measuring", "Camlin", "SUP-010", 12.0, 25.0, 50, 3650),
        ])
    ]

    products = []
    prod_counter = 1
    for category, items in categories_spec:
        for p_name, subcat, brand, sup_id, c_price, s_price, rop, s_life in items:
            p_id = f"PRD-{prod_counter:03d}"
            sku = f"SKU-{p_name[:3].upper()}-{prod_counter:03d}"
            products.append({
                "product_id": p_id,
                "sku": sku,
                "product_name": p_name,
                "category": category,
                "subcategory": subcat,
                "brand": brand,
                "supplier_id": sup_id,
                "cost_price": c_price,
                "selling_price": s_price,
                "reorder_point": rop,
                "shelf_life_days": s_life
            })
            prod_counter += 1

    sup_map = {s["supplier_id"]: s for s in suppliers}

    # 4. TIME HORIZON
    start_date = date(2024, 6, 1)
    days_count = 90
    dates_list = [start_date + timedelta(days=i) for i in range(days_count)]

    # 5. INITIAL STOCK LEVEL CONFIGURATION
    store_stock = {}
    for st in stores:
        s_id = st["store_id"]
        store_stock[s_id] = {}
        for p in products:
            p_id = p["product_id"]
            cat = p["category"]
            rop = p["reorder_point"]

            if p_id == "PRD-004" and s_id == "STR-001":
                # Stockout risk item
                init_qty = 50
            elif p_id == "PRD-093" and s_id == "STR-002":
                # Overstocked Linen Kurta
                init_qty = 220
            elif p_id == "PRD-098":
                # Dead stock calligraphy set
                init_qty = 30
            elif p_id == "PRD-001" and s_id == "STR-003":
                # Stockout gap scenario
                init_qty = 140
            elif cat in ["Beverages", "Snacks", "Grocery"]:
                init_qty = rop * random.randint(3, 5)
            elif cat in ["Electronics Accessories"]:
                init_qty = rop * 4 if s_id == "STR-004" else rop * 2
            else:
                init_qty = rop * random.randint(2, 4)
            store_stock[s_id][p_id] = init_qty

    # Base daily demand rates
    base_demand = {}
    for p in products:
        p_id = p["product_id"]
        cat = p["category"]
        if p_id == "PRD-004": # Cold pressed juice (fast moving)
            base = 10.0
        elif p_id == "PRD-001": # Masala chai
            base = 11.5
        elif p_id == "PRD-003": # Electrolyte drink
            base = 3.5 # will surge to 22 in monsoon
        elif p_id == "PRD-019": # Dark chocolate
            base = 7.5 # will collapse after day 45
        elif p_id == "PRD-065": # USB-C cable
            base = 3.0 # store 4 multiplier gives 18.0
        elif p_id == "PRD-093": # Kurta
            base = 0.25 # very low sales rate
        elif p_id == "PRD-097": # Heavy stapler
            base = 0.20
        elif p_id == "PRD-098": # Calligraphy set
            base = 0.00 # dead stock
        elif cat in ["Beverages", "Snacks"]:
            base = random.uniform(3.0, 7.5)
        elif cat in ["Grocery"]:
            base = random.uniform(2.5, 6.0)
        elif cat in ["Personal Care", "Home Care"]:
            base = random.uniform(1.5, 4.0)
        elif cat in ["Electronics Accessories"]:
            base = random.uniform(1.2, 3.5)
        else:
            base = random.uniform(0.3, 1.8)
        base_demand[p_id] = base

    # Deliveries map by arrival date
    pending_deliveries = defaultdict(list)
    purchase_orders = []
    po_id_counter = 1
    tx_id_counter = 1

    # On-order pipeline tracking: (store_id, product_id) -> total on order
    on_order = defaultdict(int)

    # 6. INVENTORY SIMULATION LOOP OVER 90 DAYS
    sales_records = []
    inventory_records = []

    for day_idx, current_day in enumerate(dates_list):
        day_str = current_day.isoformat()

        # A. Process today's deliveries
        todays_deliveries = pending_deliveries[current_day]
        delivered_map = defaultdict(int)
        for deliv in todays_deliveries:
            key = (deliv["store_id"], deliv["product_id"])
            delivered_map[key] += deliv["quantity"]
            on_order[key] -= deliv["quantity"]
            if on_order[key] < 0:
                on_order[key] = 0

        # B. Process sales and inventory updates for every store & product
        for st in stores:
            s_id = st["store_id"]
            is_tech_hub = (s_id == "STR-004")

            for p in products:
                p_id = p["product_id"]
                p_cat = p["category"]
                opening = store_stock[s_id][p_id]
                received = delivered_map[(s_id, p_id)]
                available = opening + received

                base = base_demand[p_id]

                # Store divergence scenario 6
                if p_id in ["PRD-065", "PRD-066", "PRD-067"]:
                    store_mult = 5.2 if is_tech_hub else 0.55
                else:
                    store_mult = 1.0

                # Weekend factor
                dow_factor = 1.30 if current_day.weekday() in [4, 5, 6] else 0.88

                # Scenario 4: Sales spike for PRD-003 between days 35 and 52
                spike_factor = 1.0
                if p_id == "PRD-003" and 35 <= day_idx <= 52:
                    spike_factor = 5.2

                # Scenario 5: Sales drop for PRD-019 after day 45
                drop_factor = 1.0
                if p_id == "PRD-019" and day_idx > 45:
                    drop_factor = 0.12

                # Scenario 9: Dead stock PRD-098
                if p_id == "PRD-098":
                    target_units = 0
                else:
                    mu = base * store_mult * dow_factor * spike_factor * drop_factor
                    target_units = max(0, int(random.gauss(mu, 1.1)))

                # Scenario 10: Stockout constraint - sold cannot exceed available
                sold = min(available, target_units)

                # Damaged and returned
                damaged = 0
                returned = 0
                adjustment = 0

                if sold > 0 and random.random() < 0.035:
                    returned = 1
                if available > 0 and random.random() < 0.012:
                    damaged = 1
                if day_idx % 14 == 0 and random.random() < 0.015:
                    adjustment = random.choice([-1, 1])

                closing = opening + received - sold + returned - damaged + adjustment
                if closing < 0:
                    adjustment += (-closing)
                    closing = 0

                store_stock[s_id][p_id] = closing

                # Log inventory snapshot
                inventory_records.append({
                    "date": day_str,
                    "store_id": s_id,
                    "product_id": p_id,
                    "opening_stock": opening,
                    "received_quantity": received,
                    "sold_quantity": sold,
                    "returned_quantity": returned,
                    "damaged_quantity": damaged,
                    "adjustment_quantity": adjustment,
                    "closing_stock": closing
                })

                # Log individual sales transactions
                if sold > 0:
                    remaining = sold
                    while remaining > 0:
                        basket = min(remaining, random.randint(1, min(4, remaining)))
                        disc = round(p["selling_price"] * basket * (0.10 if random.random() < 0.12 else 0.0), 2)
                        sales_records.append({
                            "transaction_id": f"TXN-{tx_id_counter:07d}",
                            "date": day_str,
                            "store_id": s_id,
                            "product_id": p_id,
                            "quantity": basket,
                            "unit_price": p["selling_price"],
                            "discount_amount": disc
                        })
                        tx_id_counter += 1
                        remaining -= basket

                # C. Check Replenishment Triggers (Realistic Store Procurement)
                sup = sup_map[p["supplier_id"]]
                lead = sup["lead_time_days"]
                rop = p["reorder_point"]
                moq = sup["minimum_order_quantity"]
                total_pipeline = closing + on_order[(s_id, p_id)]

                # Exclusions for targeted scenarios:
                # 1. PRD-004 at STR-001: Suppress reorders after day 60 to produce critical stockout risk on day 90!
                if p_id == "PRD-004" and s_id == "STR-001" and day_idx > 60:
                    continue
                # 2. PRD-093 at STR-002: Overstocked, no reorders needed
                if p_id == "PRD-093":
                    continue
                # 3. PRD-098: Dead stock, no reorders needed
                if p_id == "PRD-098":
                    continue
                # 4. PRD-001 at STR-003: Suppress reorders during days 20 to 38 to induce stockout gap, then place recovery PO
                if p_id == "PRD-001" and s_id == "STR-003" and 20 <= day_idx <= 38:
                    continue

                if total_pipeline <= rop and day_idx < 86:
                    # Calculate order quantity to reach target stock
                    order_qty = max(moq, rop * 3 - total_pipeline)
                    order_date = current_day
                    exp_date = order_date + timedelta(days=lead)

                    # Scenario 8: Explicit delayed PO injection for long-lead SUP-011
                    if p["supplier_id"] == "SUP-011" and day_idx in [35, 45]:
                        # DELAYED by 15 days
                        rec_date = exp_date + timedelta(days=15)
                        status = "RECEIVED" if rec_date <= dates_list[-1] else "DELAYED"
                    elif random.random() < 0.08:
                        # Random occasional supplier delay (3-5 days)
                        rec_date = exp_date + timedelta(days=random.randint(3, 6))
                        status = "RECEIVED" if rec_date <= dates_list[-1] else "DELAYED"
                    else:
                        rec_date = exp_date
                        status = "RECEIVED" if rec_date <= dates_list[-1] else "PENDING"

                    po_rec = {
                        "po_id": f"PO-2024-{po_id_counter:04d}",
                        "order_date": order_date.isoformat(),
                        "expected_date": exp_date.isoformat(),
                        "received_date": rec_date.isoformat() if status == "RECEIVED" else "",
                        "supplier_id": p["supplier_id"],
                        "store_id": s_id,
                        "product_id": p_id,
                        "ordered_quantity": order_qty,
                        "received_quantity": order_qty if status == "RECEIVED" else 0,
                        "status": status,
                        "unit_cost": p["cost_price"]
                    }
                    po_id_counter += 1
                    purchase_orders.append(po_rec)

                    on_order[(s_id, p_id)] += order_qty
                    pending_deliveries[rec_date].append({
                        "store_id": s_id,
                        "product_id": p_id,
                        "quantity": order_qty
                    })

    # WRITE CSV FILES
    stores_path = DATA_DIR / "stores.csv"
    with open(stores_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["store_id", "store_name", "city", "state", "store_type"])
        writer.writeheader()
        writer.writerows(stores)

    suppliers_path = DATA_DIR / "suppliers.csv"
    with open(suppliers_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["supplier_id", "supplier_name", "lead_time_days", "minimum_order_quantity"])
        writer.writeheader()
        writer.writerows(suppliers)

    products_path = DATA_DIR / "products.csv"
    with open(products_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "product_id", "sku", "product_name", "category", "subcategory", 
            "brand", "supplier_id", "cost_price", "selling_price", "reorder_point", "shelf_life_days"
        ])
        writer.writeheader()
        writer.writerows(products)

    po_path = DATA_DIR / "purchase_orders.csv"
    with open(po_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "po_id", "order_date", "expected_date", "received_date", "supplier_id", 
            "store_id", "product_id", "ordered_quantity", "received_quantity", "status", "unit_cost"
        ])
        writer.writeheader()
        writer.writerows(purchase_orders)

    inv_path = DATA_DIR / "inventory.csv"
    with open(inv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "store_id", "product_id", "opening_stock", "received_quantity", 
            "sold_quantity", "returned_quantity", "damaged_quantity", "adjustment_quantity", "closing_stock"
        ])
        writer.writeheader()
        writer.writerows(inventory_records)

    sales_path = DATA_DIR / "sales.csv"
    with open(sales_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "transaction_id", "date", "store_id", "product_id", "quantity", "unit_price", "discount_amount"
        ])
        writer.writeheader()
        writer.writerows(sales_records)

    print(f"Data Generation Complete!")
    print(f"- Stores: {len(stores)}")
    print(f"- Suppliers: {len(suppliers)}")
    print(f"- Products: {len(products)}")
    print(f"- Purchase Orders: {len(purchase_orders)}")
    print(f"- Inventory Snapshots: {len(inventory_records)}")
    print(f"- Sales Transactions: {len(sales_records)}")

if __name__ == "__main__":
    generate_retail_dataset()
