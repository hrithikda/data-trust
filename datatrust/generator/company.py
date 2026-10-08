"""Reference data describing the fictional company Copperleaf Coffee Co.

Copperleaf is a direct-to-consumer specialty coffee roaster selling one-off orders and
coffee subscriptions in the US, Canada and the UK. Operational data lives in five systems:
Shopfront (storefront), PayRail (payments), Rebill (subscriptions), Deskline (support) and
CampaignHub (marketing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

BUSINESS_START = date(2024, 1, 1)
"""First day of trading covered by the synthetic history."""

COMPANY_LAUNCH = date(2023, 6, 1)
"""No customer can have signed up before the storefront launched."""


@dataclass(frozen=True)
class Product:
    product_id: str
    sku: str
    name: str
    category: str
    price_cents: int
    launched_on: date
    is_active: bool = True


PRODUCTS: tuple[Product, ...] = (
    Product("P001", "CL-HB-12", "House Blend Whole Bean 12oz", "whole_bean", 1600, date(2023, 6, 1)),
    Product("P002", "CL-HB-32", "House Blend Whole Bean 2lb", "whole_bean", 3800, date(2023, 6, 1)),
    Product("P003", "CL-ER-12", "Ember Espresso Roast 12oz", "espresso", 1850, date(2023, 6, 1)),
    Product("P004", "CL-ER-32", "Ember Espresso Roast 2lb", "espresso", 4300, date(2023, 9, 1)),
    Product("P005", "CL-DK-12", "Dark Harbor French Roast 12oz", "whole_bean", 1650, date(2023, 6, 1)),
    Product("P006", "CL-DC-12", "Swiss Water Decaf 12oz", "whole_bean", 1750, date(2023, 6, 1)),
    Product("P007", "CL-ET-YRG", "Ethiopia Yirgacheffe Single Origin", "single_origin", 2200, date(2024, 2, 1)),
    Product("P008", "CL-CO-HUI", "Colombia Huila Single Origin", "single_origin", 2050, date(2024, 2, 1)),
    Product("P009", "CL-KE-NYE", "Kenya Nyeri AA Single Origin", "single_origin", 2400, date(2024, 5, 1)),
    Product("P010", "CL-GT-ANT", "Guatemala Antigua Single Origin", "single_origin", 2100, date(2024, 8, 1)),
    Product("P011", "CL-SU-RWA", "Rwanda Nyamasheke Microlot", "single_origin", 2600, date(2025, 3, 1)),
    Product("P012", "CL-GR-HB", "House Blend Ground 12oz", "ground", 1600, date(2023, 6, 1)),
    Product("P013", "CL-GR-DK", "Dark Harbor Ground 12oz", "ground", 1650, date(2023, 6, 1)),
    Product("P014", "CL-GR-DC", "Swiss Water Decaf Ground 12oz", "ground", 1750, date(2024, 1, 1)),
    Product("P015", "CL-CB-32", "Cold Brew Coarse Grind 2lb", "ground", 3600, date(2024, 4, 1)),
    Product("P016", "CL-EQ-V60", "Ceramic Pour-Over Dripper", "equipment", 2800, date(2023, 6, 1)),
    Product("P017", "CL-EQ-KTL", "Gooseneck Kettle 1L", "equipment", 6900, date(2023, 9, 1)),
    Product("P018", "CL-EQ-GRN", "Conical Burr Grinder", "equipment", 14900, date(2024, 1, 1)),
    Product("P019", "CL-EQ-FLT", "Paper Filters (100 pack)", "equipment", 900, date(2023, 6, 1)),
    Product("P020", "CL-EQ-PRS", "French Press 34oz", "equipment", 3900, date(2023, 9, 1)),
    Product("P021", "CL-MR-MUG", "Copperleaf Stoneware Mug", "merchandise", 2400, date(2023, 6, 1)),
    Product("P022", "CL-MR-TOT", "Canvas Tote Bag", "merchandise", 1800, date(2024, 3, 1)),
    Product("P023", "CL-MR-GFT", "Digital Gift Card", "gift_card", 5000, date(2023, 6, 1)),
    Product("P024", "CL-SB-CLS", "Classic Subscription Box", "subscription_box", 1800, date(2023, 6, 1)),
    Product("P025", "CL-SB-DBL", "Double Subscription Box", "subscription_box", 3200, date(2023, 6, 1)),
    Product("P026", "CL-SB-ESP", "Espresso Club Box", "subscription_box", 2600, date(2023, 9, 1)),
    Product("P027", "CL-SB-DSC", "Discovery Box (biweekly)", "subscription_box", 2200, date(2024, 2, 1)),
    Product("P028", "CL-HB-PDS", "House Blend Compostable Pods (30)", "pods", 2100, date(2025, 1, 15)),
    Product("P029", "CL-ER-PDS", "Ember Espresso Pods (30)", "pods", 2300, date(2025, 1, 15)),
    Product("P030", "CL-HL-24", "Holiday Spice Blend 12oz", "whole_bean", 1900, date(2024, 11, 1), is_active=False),
)

ONE_OFF_PRODUCTS = tuple(p for p in PRODUCTS if p.category not in {"subscription_box"})


@dataclass(frozen=True)
class Plan:
    plan_code: str
    product_id: str
    billing_interval: str  # monthly | biweekly
    price_cents: int
    weight: float


PLANS: tuple[Plan, ...] = (
    Plan("CLASSIC_MONTHLY", "P024", "monthly", 1800, 0.45),
    Plan("DOUBLE_MONTHLY", "P025", "monthly", 3200, 0.22),
    Plan("ESPRESSO_CLUB", "P026", "monthly", 2600, 0.20),
    Plan("DISCOVERY_BIWEEKLY", "P027", "biweekly", 2200, 0.13),
)

COUNTRIES = (("US", "USD", 0.072, 0.72), ("CA", "CAD", 0.05, 0.16), ("GB", "GBP", 0.0, 0.12))
"""(country_code, currency, sales tax rate, share of customers). UK prices are VAT-inclusive."""

ACQUISITION_CHANNELS = (
    ("organic_search", 0.24),
    ("paid_search", 0.18),
    ("paid_social", 0.17),
    ("referral", 0.12),
    ("email", 0.08),
    ("affiliate", 0.07),
    ("influencer", 0.06),
    ("direct", 0.08),
)

CAMPAIGN_CHANNELS = ("paid_search", "paid_social", "email", "affiliate", "influencer")
CAMPAIGN_THEMES = (
    "New Year Reset", "Valentine Pour-Over", "Spring Single Origins", "Mother's Day Gifting",
    "Cold Brew Season", "Summer Espresso", "Back to Routine", "Harvest Microlots",
    "Black Friday", "Holiday Gifting", "Subscription Saver", "Refer a Friend",
)

PAYMENT_METHODS = (("card", 0.70), ("paypal", 0.15), ("apple_pay", 0.15))

TICKET_CHANNELS = (("email", 0.45), ("chat", 0.35), ("phone", 0.12), ("social", 0.08))
TICKET_PRIORITIES = (("low", 0.30), ("normal", 0.50), ("high", 0.15), ("urgent", 0.05))
REFUND_REASONS = ("damaged_in_transit", "wrong_item", "late_delivery", "quality_complaint", "order_returned")

FIRST_NAMES = (
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Elijah", "Sophia", "Mateo", "Isabella", "Lucas",
    "Mia", "Levi", "Amelia", "Ezra", "Harper", "Asher", "Evelyn", "Leo", "Abigail", "Hudson",
    "Priya", "Arjun", "Mei", "Hiroshi", "Fatima", "Omar", "Chloe", "Owen", "Grace", "Samuel",
    "Zoe", "Nathan", "Aria", "Caleb", "Nora", "Isaac", "Layla", "Julian", "Riley", "Theo",
)
LAST_NAMES = (
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson",
    "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Clark", "Lewis", "Patel", "Nguyen",
    "Kim", "Singh", "Chen", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Murphy",
)
EMAIL_DOMAINS = ("gmail.com", "outlook.com", "icloud.com", "yahoo.com", "proton.me", "hey.com")
