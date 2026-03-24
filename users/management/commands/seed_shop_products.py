from pathlib import Path
import os
import shutil
from urllib.parse import quote

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from users.models import Product


PRODUCT_SEED_DATA = [
    {
        "name": "AHJU Eco Friendly Bamboo Card",
        "category": "Cards",
        "description": "Ecofriendly. Luxurious.\n\nWhat you get: A sleek bamboo card with NFC chip embedded inside. Instantly shares your digital profile with one tap.\n\nBenefit: Show authority and confidence. Make every introduction unforgettable. Perfect for executives, consultants, creatives and anyone who wants to make a lasting impression.\n\nCustomization: You just need to choose your preferred template from our list and send us your details i.e. logo, card design, name, position etc. (details will be provided via email sent after purchase.)",
        "price": 35000,
        "old_price": None,
        "image_filename": "AHJU eco friendly bamboo card.png",
        "gallery_filenames": [
            "AHJU eco friendly bamboo card.png",
            "bamboo image 1.jpg",
            "bamboo image 2.jpg",
        ],
    },
    {
        "name": "AHJU Social Tags",
        "category": "Tags",
        "description": "With the AHJU Social Tag Keychain, getting followers has never been easier.\n\nJust tap it on any smartphone and your Instagram, X, LinkedIn, Facebook — or any social media you choose — pops up instantly.\n\nOne-tap follow – No typing, no stress, just instant connection to your page.\nQR fallback – Even if their phone doesn’t support NFC, they can scan the QR code to reach you.\nAny platform you want – Works with Instagram, X, LinkedIn, Facebook, TikTok, or any social link.\nAlways with you – Clip it to your keys, bag, or lanyard so you never miss a chance to connect.\n\nStand out, connect faster, and turn every meet into a new follower.\n\nCustomization: Customization details will be provided via email sent after purchase.",
        "price": 20000,
        "old_price": None,
        "image_filename": "AHJU social tag.png",
        "gallery_filenames": [
            "AHJU social tag.png",
            "social tag image 1.png",
            "social tag image 2.png",
        ],
    },
    {
        "name": "AHJU Social Tag – Dome Stickers",
        "category": "Stickers",
        "description": "Glossy dome sticker tags designed for smooth placement on products, packaging, and surfaces where you want durable smart sharing.",
        "price": 15000,
        "old_price": None,
        "image_filename": "AHJU Social Tag – Dome Stickers.png",
        "gallery_filenames": [
            "AHJU Social Tag – Dome Stickers.png",
            "AHJU Sticker Tag.jpg",
        ],
    },
    {
        "name": "AHJU Google Review Card",
        "category": "Cards",
        "description": "Grow your business reputation with one tap.\n\nWhat you get: NFC card that links directly to your Google Review page. Customers tap and leave a review instantly.\n\nBenefit: Boost your online reputation and get more customers without asking twice. Perfect for restaurants, salons, clinics, and small businesses.\n\nCustomization: Customize with your logo, design, and details or choose from templates available via email sent after purchase.",
        "price": 40000,
        "old_price": None,
        "image_filename": "ahju Google review card image 1.png",
        "gallery_filenames": [
            "ahju Google review card image 1.png",
            "ahju google review card image 2.png",
        ],
    },
    {
        "name": "AHJU NFC Sticker",
        "category": "Stickers",
        "description": "Simple and versatile NFC sticker for quick tap-to-open actions—ideal for laptops, counters, phones, and marketing touchpoints.",
        "price": 15000,
        "old_price": None,
        "image_filename": "AHJU NFC Sticker.png",
        "gallery_filenames": [
            "AHJU NFC Sticker.png",
            "AHJU Sticker Tag.jpg",
        ],
    },
    {
        "name": "AHJU KeyTag",
        "category": "Tags",
        "description": "Portable keytag form factor that keeps your digital profile and business links with you everywhere, ready for instant sharing.",
        "price": 20000,
        "old_price": None,
        "image_filename": "AHJU KeyTag.png",
        "gallery_filenames": [
            "AHJU KeyTag.png",
            "AHJU Key Tag.jpg",
        ],
    },
    {
        "name": "AHJU Premium Black Card",
        "category": "Cards",
        "description": "A sleek black premium smart card built for standout first impressions, combining elegant design with instant digital connectivity.",
        "price": 35000,
        "old_price": None,
        "image_filename": "AHJU Premium Black Card.png",
        "gallery_filenames": [
            "AHJU Premium Black Card.png",
            "AHJU Black Card.jpg",
            "AHJU Black PVC Card.jpg",
        ],
    },
    {
        "name": "AHJU Classic White Card",
        "category": "Cards",
        "description": "The timeless AHJU card. Clean, sleek, and professional in crisp white PVC.\n\nWhat you get: A premium PVC card (front + back design) with built-in NFC. One tap instantly shares your contact details, website, store, portfolio, or social links.\n\nBenefit: Never run out of cards again. Stand out at meetings, networking events, or pop-ups. Perfect for professionals and entrepreneurs who want simple sophistication.\n\nCustomization: Customize with your logo, design, and details via email sent after purchase.\n\nOnce you purchase your card, a link will be provided via after-sales email, you’ll get access to create your digital profile and your card will begin to undergo production.",
        "price": 29999,
        "old_price": 35000,
        "image_filename": "AHJU Classic White Card.jpg",
        "gallery_filenames": [
            "AHJU Classic White Card.jpg",
            "white card image 1.png",
            "white card image 2.png",
        ],
    },
]


class Command(BaseCommand):
    help = "Seed shop products and copy product images into backend media."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-dir",
            type=str,
            default="",
            help="Optional absolute/relative path where source product images are located.",
        )

    def handle(self, *args, **options):
        source_dir = (options.get("source_dir") or "").strip()
        fallback_base_url = (
            os.getenv("PRODUCT_IMAGE_FALLBACK_BASE_URL", "").strip()
            or "https://raw.githubusercontent.com/TEESTIMONY/ahju/main"
        ).rstrip("/")

        def build_fallback_url(filename: str) -> str:
            return f"{fallback_base_url}/{quote(filename)}"

        if source_dir:
            source_root = Path(source_dir).resolve()
        else:
            source_root = (Path(settings.BASE_DIR).parent / "ahju").resolve()

        if not source_root.exists():
            self.stderr.write(self.style.ERROR(f"Source directory not found: {source_root}"))
            return

        media_root = Path(settings.MEDIA_ROOT).resolve()
        product_media_dir = media_root / "products"
        product_media_dir.mkdir(parents=True, exist_ok=True)

        created = 0
        updated = 0
        missing_images = []

        for item in PRODUCT_SEED_DATA:
            slug = slugify(item["name"])
            existing_product = Product.objects.filter(slug=slug).first()
            source_image_path = source_root / item["image_filename"]
            target_image_path = product_media_dir / item["image_filename"]
            image_file_value = None

            if source_image_path.exists():
                # Avoid SameFileError when source and target point to the same file.
                if source_image_path.resolve() != target_image_path.resolve():
                    shutil.copy2(source_image_path, target_image_path)
                image_file_value = f"products/{item['image_filename']}"
                image_url = f"{settings.MEDIA_URL.rstrip('/')}/products/{item['image_filename']}"
            elif target_image_path.exists():
                # Keep using already-present media file (e.g. persistent disk in production).
                image_file_value = f"products/{item['image_filename']}"
                image_url = f"{settings.MEDIA_URL.rstrip('/')}/products/{item['image_filename']}"
            elif existing_product and getattr(existing_product, "image", None):
                # Preserve existing uploaded image in DB.
                image_file_value = existing_product.image.name
                image_url = existing_product.image_url
            elif existing_product and (existing_product.image_url or "").strip():
                # Do not wipe existing product image URL when source files are not available.
                image_url = existing_product.image_url
            else:
                # Last fallback: point to repository-hosted image URL so first deploy still shows media.
                image_url = build_fallback_url(item["image_filename"])
                missing_images.append(item["image_filename"])

            gallery_images = []
            for gallery_filename in item.get("gallery_filenames", []):
                source_gallery_path = source_root / gallery_filename
                target_gallery_path = product_media_dir / gallery_filename
                if source_gallery_path.exists():
                    # Avoid SameFileError when source and target are identical.
                    if source_gallery_path.resolve() != target_gallery_path.resolve():
                        shutil.copy2(source_gallery_path, target_gallery_path)
                    gallery_images.append(
                        f"{settings.MEDIA_URL.rstrip('/')}/products/{gallery_filename}"
                    )
                elif target_gallery_path.exists():
                    gallery_images.append(
                        f"{settings.MEDIA_URL.rstrip('/')}/products/{gallery_filename}"
                    )
                else:
                    gallery_images.append(build_fallback_url(gallery_filename))
                    missing_images.append(gallery_filename)

            if not gallery_images and existing_product and existing_product.gallery_images:
                # Preserve existing gallery when no source/target files were found.
                gallery_images = existing_product.gallery_images

            defaults = {
                "name": item["name"],
                "category": item["category"],
                "description": item.get("description", ""),
                "price": item["price"],
                "old_price": item["old_price"],
                "image": image_file_value,
                "image_url": image_url,
                "gallery_images": gallery_images,
                "is_active": True,
                "stock_quantity": 100,
            }

            product, was_created = Product.objects.update_or_create(
                slug=slug,
                defaults=defaults,
            )

            if was_created:
                created += 1
            else:
                updated += 1

            self.stdout.write(f"Seeded: {product.name} ({product.slug})")

        self.stdout.write(self.style.SUCCESS(f"Done. Created: {created}, Updated: {updated}"))

        if missing_images:
            self.stdout.write(
                self.style.WARNING(
                    "Missing source images (product created without image): "
                    + ", ".join(missing_images)
                )
            )
