#!/usr/bin/env sh
set -eu

echo "[deploy] Running migrations..."
python manage.py migrate

MEDIA_ROOT_DIR="${DJANGO_MEDIA_ROOT:-media}"
mkdir -p "$MEDIA_ROOT_DIR"

echo "[deploy] Collecting static files..."
python manage.py collectstatic --noinput

# Optional auto-seed (enabled by default)
SEED_PRODUCTS_ON_DEPLOY="${SEED_PRODUCTS_ON_DEPLOY:-true}"
if [ "$SEED_PRODUCTS_ON_DEPLOY" = "true" ]; then
  SOURCE_DIR="${PRODUCT_IMAGE_SOURCE_DIR:-}"

  if [ -z "$SOURCE_DIR" ]; then
    for candidate in \
      "/app/media/products" \
      "/opt/render/project/src/media/products" \
      "$(pwd)/media/products"; do
      if [ -d "$candidate" ]; then
        SOURCE_DIR="$candidate"
        break
      fi
    done
  fi

  if [ -z "$SOURCE_DIR" ] || [ ! -d "$SOURCE_DIR" ]; then
    SOURCE_DIR="$(pwd)"
  fi

  echo "[deploy] Seeding products from source dir: $SOURCE_DIR"
  python manage.py seed_shop_products --source-dir "$SOURCE_DIR" || echo "[deploy] WARNING: Product seeding failed; app will still start."
else
  echo "[deploy] Skipping product seed (SEED_PRODUCTS_ON_DEPLOY=$SEED_PRODUCTS_ON_DEPLOY)"
fi

echo "[deploy] Starting gunicorn..."
gunicorn config.wsgi:application --bind 0.0.0.0:${PORT}
