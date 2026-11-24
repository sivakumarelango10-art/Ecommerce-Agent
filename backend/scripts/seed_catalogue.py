"""Idempotent product catalogue seed script for StoreFlow.

Populates initial categories, products, and inventory in the staging database.
Includes strict database-target protection:
- Mandatory staging database hostname allowlist (STORE_FLOW_STAGING_DATABASE_HOST).
- Strict PostgreSQL driver requirement.
- Rejection of protected development and test databases.
- Validation of both DATABASE_URL and DATABASE_URL_UNPOOLED when configured.
- Explicit staging confirmation requirement (--confirm-staging).
- Transactional dry-run verification mode (--dry-run).
- Strict credential concealment in all errors and logs.

Execution Modes (mutually exclusive):
1. Dry-run mode (--dry-run):
   - Requires an active database connection to the approved staging database.
   - Executes transactional INSERT statements using transaction flushes to validate
     all constraints, foreign keys, and unique indices against the target schema.
   - Explicitly rolls back the transaction upon completion; zero changes are committed or persisted.
   - WARNING: This is NOT read-only and NOT an offline simulation. It executes active
     transactional writes within an uncommitted transaction.
2. Live mode (--confirm-staging):
   - Requires explicit '--confirm-staging' flag.
   - Connects to the approved staging database, executes inserts, and explicitly commits records.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import logging
import os
import sys
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.orm import Session

from backend.app.db.models import Inventory, Product, ProductCategory
from backend.app.db.session import create_database_engine, create_session_factory

logger = logging.getLogger(__name__)

STAGING_ALLOWLIST_ENV_VAR = "STORE_FLOW_STAGING_DATABASE_HOST"
STAGING_DB_NAME_ENV_VAR = "STORE_FLOW_STAGING_DATABASE_NAME"
APPROVED_STAGING_DATABASE_NAME = "neondb"

# --- Demo Catalogue Data (26 products across 6 categories) ---

DEMO_CATEGORIES: list[dict[str, str]] = [
    {"name": "Smartphones", "slug": "smartphones"},
    {"name": "Laptops", "slug": "laptops"},
    {"name": "Audio & Headphones", "slug": "audio"},
    {"name": "Accessories", "slug": "accessories"},
    {"name": "Smartwatches", "slug": "smartwatches"},
    {"name": "Home Electronics", "slug": "home-electronics"},
]

DEMO_PRODUCTS: list[dict[str, object]] = [
    # 1. Smartphones
    {
        "sku": "PHONE-APEX-PRO",
        "name": "Apex Pro 5G Smartphone (256GB)",
        "description": "Flagship 5G smartphone with 6.7-inch 120Hz AMOLED display, triple 50MP camera setup, and 5000mAh battery with 80W fast charging.",
        "price": Decimal("49999.00"),
        "categories": ["smartphones"],
        "available_quantity": 30,
    },
    {
        "sku": "PHONE-NOVA-5G",
        "name": "Nova 5G Budget Smartphone (128GB)",
        "description": "Affordable 5G smartphone featuring high refresh rate display, 48MP dual camera, and all-day 5000mAh battery life.",
        "price": Decimal("17999.00"),
        "categories": ["smartphones"],
        "available_quantity": 45,
    },
    {
        "sku": "PHONE-TITAN-ULTRA",
        "name": "Titan Ultra Max Smartphone (512GB)",
        "description": "Premium ultra smartphone with ceramic casing, 200MP optical zoom camera, 16GB RAM, and Snapdragon 8 series processor.",
        "price": Decimal("79999.00"),
        "categories": ["smartphones"],
        "available_quantity": 15,
    },
    {
        "sku": "PHONE-ZENITH-CMP",
        "name": "Zenith Compact 5G Smartphone (128GB)",
        "description": "Pocket-friendly 6.1-inch FHD+ OLED smartphone with premium aluminum frame, stereo speakers, and wireless charging support.",
        "price": Decimal("29999.00"),
        "categories": ["smartphones"],
        "available_quantity": 25,
    },

    # 2. Laptops
    {
        "sku": "LAPTOP-AERO-15",
        "name": "AeroBook Pro 15.6-inch Laptop",
        "description": "High-performance ultrabook with Intel Core i7, 16GB DDR5 RAM, 512GB NVMe SSD, and backlit keyboard for professionals.",
        "price": Decimal("68999.00"),
        "categories": ["laptops"],
        "available_quantity": 20,
    },
    {
        "sku": "LAPTOP-FEATHER-13",
        "name": "FeatherLite 13.3-inch Thin & Light Laptop",
        "description": "Slim all-aluminum laptop with 14-hour battery life, 16GB RAM, 512GB SSD, and vibrant 2K anti-glare display weighing just 1.1kg.",
        "price": Decimal("52999.00"),
        "categories": ["laptops"],
        "available_quantity": 25,
    },
    {
        "sku": "LAPTOP-VORTEX-16",
        "name": "Vortex Gaming Laptop 16-inch 165Hz",
        "description": "Dedicated gaming laptop powered by NVIDIA GeForce RTX 4060, AMD Ryzen 7 processor, 16GB DDR5 RAM, and dual-fan liquid cooling.",
        "price": Decimal("94999.00"),
        "categories": ["laptops"],
        "available_quantity": 12,
    },
    {
        "sku": "LAPTOP-INSPIRE-14",
        "name": "Inspire 14-inch Everyday Student Laptop",
        "description": "Dependable student and home laptop with Intel Core i3, 8GB RAM, 512GB SSD, HD webcam, and pre-installed productivity suite.",
        "price": Decimal("34999.00"),
        "categories": ["laptops"],
        "available_quantity": 40,
    },

    # 3. Audio & Headphones
    {
        "sku": "AUDIO-WH-ANC500",
        "name": "SoundSilence ANC Wireless Headphones",
        "description": "Over-ear active noise cancelling Bluetooth headphones with 40-hour battery life, 40mm titanium drivers, and plush memory foam earcups.",
        "price": Decimal("8999.00"),
        "categories": ["audio"],
        "available_quantity": 50,
    },
    {
        "sku": "AUDIO-TWS-PRO1",
        "name": "ClearTone Pro True Wireless Earbuds",
        "description": "In-ear TWS earbuds with hybrid active noise cancellation, quad beamforming microphones, and IPX5 water resistance.",
        "price": Decimal("3499.00"),
        "categories": ["audio"],
        "available_quantity": 60,
    },
    {
        "sku": "AUDIO-NECK-BASS",
        "name": "BassRhythm Wireless Neckband Earphones",
        "description": "Ergonomic magnetic neckband earphones with 10mm dynamic bass drivers, fast Type-C charging, and 24-hour continuous playback.",
        "price": Decimal("1299.00"),
        "categories": ["audio"],
        "available_quantity": 80,
    },
    {
        "sku": "AUDIO-SPK-360",
        "name": "Pulse 360 Portable Bluetooth Speaker",
        "description": "Rugged IP67 dust and waterproof wireless speaker delivering 360-degree immersive sound, rich bass, and 12-hour playtime.",
        "price": Decimal("2999.00"),
        "categories": ["audio"],
        "available_quantity": 35,
    },
    {
        "sku": "AUDIO-SBAR-21",
        "name": "AcousticWave 2.1 Channel Soundbar with Subwoofer",
        "description": "Home entertainment soundbar with dedicated wireless subwoofer, 120W peak power output, HDMI ARC, Bluetooth, and optical input.",
        "price": Decimal("6999.00"),
        "categories": ["audio"],
        "available_quantity": 18,
    },

    # 4. Accessories
    {
        "sku": "ACC-GAN-65W",
        "name": "HyperCharge 65W GaN Fast Wall Charger",
        "description": "Compact 3-port gallium nitride (GaN) fast charger with dual USB-C Power Delivery and USB-A ports for laptops, tablets, and phones.",
        "price": Decimal("1999.00"),
        "categories": ["accessories"],
        "available_quantity": 75,
    },
    {
        "sku": "ACC-CBL-100W",
        "name": "Braided USB-C to USB-C 100W Cable (2m)",
        "description": "Heavy-duty double-braided nylon charging and high-speed data transfer cable with anodized aluminum connectors and E-marker chip.",
        "price": Decimal("499.00"),
        "categories": ["accessories"],
        "available_quantity": 100,
    },
    {
        "sku": "ACC-PB-20K",
        "name": "PowerVault 20000mAh 22.5W Power Bank",
        "description": "High-capacity power bank with smart LED percentage display, two-way fast charging, and simultaneous triple-device output.",
        "price": Decimal("2199.00"),
        "categories": ["accessories"],
        "available_quantity": 50,
    },
    {
        "sku": "ACC-STAND-ALU",
        "name": "ErgoLift Adjustable Aluminum Laptop Stand",
        "description": "Ergonomic foldable aluminum riser for laptops and tablets up to 17 inches with heat-dissipating airflow design and silicone pads.",
        "price": Decimal("1499.00"),
        "categories": ["accessories"],
        "available_quantity": 40,
    },
    {
        "sku": "ACC-MOUSE-DUAL",
        "name": "SilentGlide Dual-Mode Wireless Mouse",
        "description": "Whisper-quiet optical wireless mouse with 2.4GHz USB receiver and Bluetooth connectivity, adjustable DPI, and rechargeable battery.",
        "price": Decimal("899.00"),
        "categories": ["accessories"],
        "available_quantity": 65,
    },

    # 5. Smartwatches
    {
        "sku": "WATCH-ACTV-GPS",
        "name": "ActiveFit Pro GPS Smartwatch",
        "description": "All-round fitness smartwatch with built-in multi-system GPS, 1.43-inch AMOLED display, SpO2 sensor, and 100+ workout modes.",
        "price": Decimal("4999.00"),
        "categories": ["smartwatches"],
        "available_quantity": 45,
    },
    {
        "sku": "WATCH-CHRONOS-BT",
        "name": "Chronos Elite Bluetooth Calling Smartwatch",
        "description": "Classic metallic round smartwatch with high-definition Bluetooth calling, 1.85-inch display, health tracking, and 7-day battery.",
        "price": Decimal("2999.00"),
        "categories": ["smartwatches"],
        "available_quantity": 55,
    },
    {
        "sku": "WATCH-TREK-5ATM",
        "name": "Trekker Outdoor Rugged Smartwatch",
        "description": "Military-grade shock-resistant and 5ATM waterproof adventure watch equipped with compass, altimeter, and rugged silicone strap.",
        "price": Decimal("7499.00"),
        "categories": ["smartwatches"],
        "available_quantity": 20,
    },
    {
        "sku": "WATCH-VITAL-BND",
        "name": "VitalBand Slim Health & Sleep Tracker",
        "description": "Ultralight activity wristband with continuous 24/7 heart rate monitoring, scientific sleep tracking, and 14-day battery life.",
        "price": Decimal("1999.00"),
        "categories": ["smartwatches"],
        "available_quantity": 70,
    },

    # 6. Home Electronics
    {
        "sku": "HOME-SMART-PLUG",
        "name": "SmartPlug 16A Wi-Fi Socket with Energy Monitor",
        "description": "Voice-enabled 16A smart plug compatible with Alexa and Google Assistant with scheduling, timer, and live power consumption tracking.",
        "price": Decimal("999.00"),
        "categories": ["home-electronics"],
        "available_quantity": 85,
    },
    {
        "sku": "HOME-LED-STRIP5",
        "name": "GlowSync RGB Smart LED Light Strip (5m)",
        "description": "Wi-Fi and Bluetooth smart ambient light strip with 16 million colors, music synchronization, and customizable app scenes.",
        "price": Decimal("1499.00"),
        "categories": ["home-electronics"],
        "available_quantity": 50,
    },
    {
        "sku": "HOME-CAM-2K360",
        "name": "GuardView 2K 360-Degree Wi-Fi Security Camera",
        "description": "Smart indoor security camera featuring 2K resolution, 360-degree pan-tilt coverage, infrared night vision, and two-way talk.",
        "price": Decimal("2499.00"),
        "categories": ["home-electronics"],
        "available_quantity": 40,
    },
    {
        "sku": "HOME-AIR-HEPA",
        "name": "PureBreeze Desktop HEPA Air Purifier",
        "description": "Compact True HEPA air purifier with 3-stage filtration capturing 99.97% of airborne particles, dust, allergens, and odors.",
        "price": Decimal("4499.00"),
        "categories": ["home-electronics"],
        "available_quantity": 22,
    },
]


@dataclass(frozen=True)
class SeedSummary:
    categories_created: int
    categories_existing: int
    products_created: int
    products_skipped: int
    inventory_created: int
    is_dry_run: bool


def sanitize_database_url(raw_url: str) -> str:
    """Mask credentials in database URL for safe logging and error reporting."""
    try:
        parsed = make_url(raw_url)
        return parsed.render_as_string(hide_password=True)
    except Exception:
        return "[sanitized-database-url]"


def parse_staging_hostname_allowlist(allowlist_raw: str | None) -> set[str]:
    """Parse and validate the STORE_FLOW_STAGING_DATABASE_HOST environment variable.

    Returns a set of lowercased, trimmed approved hostnames.
    Raises ValueError if absent, empty, or malformed.
    """
    if allowlist_raw is None:
        raise ValueError(
            f"Safety violation: Staging database hostname allowlist '{STAGING_ALLOWLIST_ENV_VAR}' is absent. "
            "Catalogue seeding execution is refused."
        )

    clean = allowlist_raw.strip()
    if not clean:
        raise ValueError(
            f"Safety violation: Staging database hostname allowlist '{STAGING_ALLOWLIST_ENV_VAR}' is empty. "
            "Catalogue seeding execution is refused."
        )

    approved: set[str] = set()
    for item in clean.split(","):
        host = item.strip().lower()
        if not host:
            raise ValueError(
                f"Safety violation: Staging database hostname allowlist '{STAGING_ALLOWLIST_ENV_VAR}' is malformed "
                "(contains empty host segment)."
            )
        # Check for invalid characters in hostnames (e.g., /, :, @, spaces)
        if any(c in host for c in ("/", ":", "@", " ", "\\")):
            raise ValueError(
                f"Safety violation: Staging database hostname allowlist contains malformed hostname '{host}'."
            )
        approved.add(host)

    if not approved:
        raise ValueError(
            f"Safety violation: Staging database hostname allowlist '{STAGING_ALLOWLIST_ENV_VAR}' is empty."
        )

    return approved


def parse_staging_database_name(db_name_raw: str | None) -> str:
    """Parse and validate the STORE_FLOW_STAGING_DATABASE_NAME environment variable or parameter.

    Requires the value to be present, non-empty, and exactly match APPROVED_STAGING_DATABASE_NAME ("neondb")
    case-insensitively. Rejects all other database names, protected names, and malformed inputs.
    Always returns the canonical constant APPROVED_STAGING_DATABASE_NAME ("neondb") after validation.
    """
    if db_name_raw is None:
        raise ValueError(
            f"Safety violation: Staging database name '{STAGING_DB_NAME_ENV_VAR}' is absent. "
            "Catalogue seeding execution is refused."
        )

    clean = db_name_raw.strip()
    if not clean:
        raise ValueError(
            f"Safety violation: Staging database name '{STAGING_DB_NAME_ENV_VAR}' is empty. "
            "Catalogue seeding execution is refused."
        )

    # Check for invalid characters in database names (e.g., /, :, @, spaces, \, ?, #)
    if any(c in clean for c in ("/", ":", "@", " ", "\\", "?", "#")):
        raise ValueError(
            f"Safety violation: Staging database name contains malformed characters '{clean}'."
        )

    clean_lower = clean.lower()
    protected_names = {"ecommerce_agent_dev", "ecommerce_agent_test"}
    if clean_lower in protected_names:
        raise ValueError(
            f"Safety violation: Staging database name '{clean}' is a protected development or test database. "
            "Catalogue seeding is strictly forbidden on dev/test databases."
        )

    if clean_lower != APPROVED_STAGING_DATABASE_NAME.lower():
        raise ValueError(
            f"Safety violation: Database name '{clean}' is not the approved staging database "
            f"'{APPROVED_STAGING_DATABASE_NAME}'. Catalogue seeding is strictly restricted to '{APPROVED_STAGING_DATABASE_NAME}'."
        )

    return APPROVED_STAGING_DATABASE_NAME


def validate_safe_target_database(
    database_url: str,
    *,
    approved_hosts: set[str] | str | None = None,
    approved_db_name: str | None = None,
) -> URL:
    """Validate that the target database URL is an approved PostgreSQL staging database.

    Enforces:
    1. Parsing strictly via SQLAlchemy make_url().
    2. PostgreSQL driver/scheme.
    3. Non-empty expected staging hostname allowlist.
    4. Exact hostname match between target URL and approved staging hostname(s).
    5. Non-empty expected staging database name (STORE_FLOW_STAGING_DATABASE_NAME).
    6. Rejection of protected development/test databases (ecommerce_agent_dev, ecommerce_agent_test).
    7. Exact, case-insensitive match between target URL database and approved staging database name.

    Never displays credentials, passwords, or tokens in raised errors.
    Returns the parsed SQLAlchemy URL on success.
    """
    if approved_hosts is None:
        raw_allowlist = os.getenv(STAGING_ALLOWLIST_ENV_VAR)
        approved_set = parse_staging_hostname_allowlist(raw_allowlist)
    elif isinstance(approved_hosts, str):
        approved_set = parse_staging_hostname_allowlist(approved_hosts)
    elif isinstance(approved_hosts, set):
        if not approved_hosts:
            raise ValueError("Safety violation: Approved staging hostname allowlist is empty.")
        approved_set = {h.strip().lower() for h in approved_hosts if h.strip()}
    else:
        approved_set = {str(h).strip().lower() for h in approved_hosts if str(h).strip()}
        if not approved_set:
            raise ValueError("Safety violation: Approved staging hostname allowlist is empty.")

    if approved_db_name is None:
        raw_db_name = os.getenv(STAGING_DB_NAME_ENV_VAR)
        approved_db = parse_staging_database_name(raw_db_name)
    else:
        approved_db = parse_staging_database_name(approved_db_name)

    # 1. Parse URL with SQLAlchemy make_url()
    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("Safety violation: Target database URL could not be parsed.")

    # 2. Require PostgreSQL driver / scheme
    if not url.drivername.startswith("postgresql"):
        raise ValueError(
            f"Safety violation: Target database driver '{url.drivername}' is not PostgreSQL. "
            "Catalogue seeding is strictly restricted to PostgreSQL staging databases."
        )

    # 3. Require valid target hostname
    target_host = (url.host or "").strip().lower()
    if not target_host:
        raise ValueError("Safety violation: Target database URL does not contain a hostname.")

    # 4. Exact match against approved staging hostname allowlist
    if target_host not in approved_set:
        raise ValueError(
            f"Safety violation: Target database hostname '{target_host}' is not in the approved staging "
            f"allowlist {sorted(approved_set)}. Refusing to connect."
        )

    # 5. Extract database name
    db_name = (url.database or "").strip("/").lower()
    if not db_name:
        raise ValueError("Safety violation: Target database URL does not contain a database name.")

    # 6. Reject protected development and test databases
    query_params = " ".join(f"{k}={v}" for k, v in url.query.items()).lower() if url.query else ""
    protected_names = {"ecommerce_agent_dev", "ecommerce_agent_test"}
    for protected in protected_names:
        if protected in db_name or protected in query_params:
            raise ValueError(
                f"Safety violation: Target database '{db_name}' contains protected name '{protected}'. "
                "Seeding must never run against development or test databases."
            )

    # 7. Exact, case-insensitive comparison against approved staging database name
    if db_name != approved_db:
        raise ValueError(
            f"Safety violation: Target database name '{db_name}' does not match approved staging "
            f"database name '{approved_db}'. Refusing to connect."
        )

    return url


def seed_catalogue(session: Session, *, dry_run: bool = False) -> SeedSummary:
    """Idempotently seed categories, products, and inventory records.

    If dry_run is True:
        - Executes transactional INSERT statements and flushes them to the database
          to validate all foreign keys, unique indices, and check constraints.
        - Explicitly rolls back the transaction without committing, ensuring zero
          data persists.
        - WARNING: Requires an active database connection; this is NOT a read-only or
          offline simulation.
    If dry_run is False:
        - Commits all changes to the database.
    """
    categories_created = 0
    categories_existing = 0
    products_created = 0
    products_skipped = 0
    inventory_created = 0

    try:
        # 1. Seed or resolve categories
        category_map: dict[str, ProductCategory] = {}
        for cat_data in DEMO_CATEGORIES:
            slug = cat_data["slug"].strip().lower()
            existing_cat = session.scalar(
                select(ProductCategory).where(
                    func.lower(ProductCategory.slug) == slug
                )
            )
            if existing_cat is not None:
                category_map[slug] = existing_cat
                categories_existing += 1
            else:
                new_cat = ProductCategory(
                    id=uuid4(),
                    name=cat_data["name"].strip(),
                    slug=slug,
                )
                session.add(new_cat)
                category_map[slug] = new_cat
                categories_created += 1

        session.flush()

        # 2. Seed products and associated inventory
        for item in DEMO_PRODUCTS:
            sku = str(item["sku"]).strip()
            existing_prod = session.scalar(
                select(Product).where(func.lower(Product.sku) == sku.lower())
            )
            if existing_prod is not None:
                products_skipped += 1
                continue

            cat_slugs: list[str] = item.get("categories", [])  # type: ignore[assignment]
            linked_cats = [category_map[s.lower()] for s in cat_slugs if s.lower() in category_map]

            new_prod = Product(
                id=uuid4(),
                sku=sku,
                name=str(item["name"]).strip(),
                description=str(item["description"]).strip(),
                price=Decimal(str(item["price"])),
                is_active=True,
                categories=linked_cats,
            )
            session.add(new_prod)
            session.flush()

            qty = int(item.get("available_quantity", 0))  # type: ignore[arg-type]
            new_inv = Inventory(
                product_id=new_prod.id,
                available_quantity=qty,
            )
            session.add(new_inv)
            products_created += 1
            inventory_created += 1

        if dry_run:
            session.rollback()
        else:
            session.commit()

        return SeedSummary(
            categories_created=categories_created,
            categories_existing=categories_existing,
            products_created=products_created,
            products_skipped=products_skipped,
            inventory_created=inventory_created,
            is_dry_run=dry_run,
        )

    except Exception:
        session.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently seed the StoreFlow product catalogue into the staging database.\n\n"
            "Execution Modes (mutually exclusive):\n"
            "  --dry-run:         Connects to the database and executes INSERT statements via\n"
            "                     transaction flushes to validate constraints, then explicitly\n"
            "                     rolls back. NOT read-only and NOT an offline simulation.\n"
            "  --confirm-staging: Live execution mode that commits catalogue records to staging."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Connect to the staging database and execute seed inserts within an uncommitted transaction, "
            "validating all constraints and foreign keys before rolling back. "
            "WARNING: Requires a real database connection and executes transactional INSERT writes; "
            "it is NOT an offline simulation or a read-only check."
        ),
    )
    mode_group.add_argument(
        "--confirm-staging",
        action="store_true",
        help="Explicit confirmation required to write demo records to the staging database and commit them.",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Target database URL. Overrides DATABASE_URL and DATABASE_URL_UNPOOLED from environment.",
    )

    try:
        args = parser.parse_args()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    # Safety Guard 1: Require explicit staging confirmation unless running in dry-run mode
    if not args.dry_run and not args.confirm_staging:
        print(
            "ERROR: Modifying the catalogue requires explicit staging confirmation.\n"
            "Pass '--confirm-staging' to apply changes to the staging database, "
            "or use '--dry-run' to preview transactional execution.",
            file=sys.stderr,
        )
        return 1

    # Safety Guard 2: Resolve and validate staging hostname allowlist and database name
    try:
        raw_allowlist = os.getenv(STAGING_ALLOWLIST_ENV_VAR)
        approved_hosts = parse_staging_hostname_allowlist(raw_allowlist)
        raw_db_name = os.getenv(STAGING_DB_NAME_ENV_VAR)
        approved_db_name = parse_staging_database_name(raw_db_name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Safety Guard 3: Validate environment configuration
    env_db_url = os.getenv("DATABASE_URL")
    env_unpooled_url = os.getenv("DATABASE_URL_UNPOOLED")

    # If both DATABASE_URL and DATABASE_URL_UNPOOLED are configured in environment, validate both
    if env_db_url and env_unpooled_url:
        try:
            validate_safe_target_database(
                env_db_url.strip().strip('"').strip("'"),
                approved_hosts=approved_hosts,
                approved_db_name=approved_db_name,
            )
            validate_safe_target_database(
                env_unpooled_url.strip().strip('"').strip("'"),
                approved_hosts=approved_hosts,
                approved_db_name=approved_db_name,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    # Resolve active target URL (override priority: --database-url > DATABASE_URL_UNPOOLED > DATABASE_URL)
    active_url_raw = (
        args.database_url
        or env_unpooled_url
        or env_db_url
    )
    if not active_url_raw or not active_url_raw.strip():
        print(
            "ERROR: No database URL provided. Set DATABASE_URL, DATABASE_URL_UNPOOLED, or pass --database-url.",
            file=sys.stderr,
        )
        return 1

    active_url_raw = active_url_raw.strip().strip('"').strip("'")

    # Safety Guard 4: Validate active target database URL against the allowlists
    try:
        validate_safe_target_database(
            active_url_raw,
            approved_hosts=approved_hosts,
            approved_db_name=approved_db_name,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Safety Guard 5: Never print raw connection string or credentials
    safe_url = sanitize_database_url(active_url_raw)

    if args.dry_run:
        print(
            f"[DRY-RUN EXECUTION] Target: {safe_url}\n"
            "Notice: Dry-run connects to the staging database and executes transactional inserts\n"
            "to validate constraints and schemas, then explicitly rolls back all changes without committing."
        )
    else:
        print(
            f"[LIVE STAGING SEED] Target: {safe_url}\n"
            "Notice: Live execution confirmed. Catalogue records will be committed to the database."
        )

    engine = None
    try:
        engine = create_database_engine(active_url_raw)
        factory = create_session_factory(engine)
        with factory() as session:
            summary = seed_catalogue(session, dry_run=args.dry_run)

        print("\nCatalogue Seeding Summary:")
        print(f"  Mode:                {'Dry-run (rolled back)' if summary.is_dry_run else 'Committed'}")
        print(f"  Categories created:  {summary.categories_created}")
        print(f"  Categories existing: {summary.categories_existing}")
        print(f"  Products created:    {summary.products_created}")
        print(f"  Products skipped:    {summary.products_skipped}")
        print(f"  Inventory created:   {summary.inventory_created}")

        return 0
    except Exception as exc:
        print(f"ERROR during seeding: {type(exc).__name__}", file=sys.stderr)
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
