# AHJU Django Backend

This backend provides initial user APIs with Firebase Google authentication.

## Endpoints

- `GET /api/health/` - health check
- `POST /api/auth/google/` - verify Firebase ID token and issue JWT
- `GET /api/users/me/` - authenticated user profile
- `PATCH /api/users/me/` - update profile (`first_name`, `last_name`)

## Setup

1. Create venv and install dependencies:

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

2. Copy env file:

```bash
copy .env.example .env
```

3. Add your Firebase service account JSON and set `FIREBASE_CREDENTIALS` in `.env`.

4. Run migrations and start server:

```bash
python manage.py migrate
python manage.py runserver
```

## Google Sign-in flow

1. Frontend authenticates user with Firebase Google sign-in.
2. Frontend gets Firebase `idToken`.
3. Frontend sends `{ "id_token": "..." }` to `POST /api/auth/google/`.
4. Backend verifies token with Firebase Admin SDK.
5. Backend creates/updates Django user and returns JWT tokens.

## Deploying backend on Render

### 1) Recommended Render setup

- Create a **Web Service** from this repo.
- Set **Root Directory** to: `backend`
- Environment: **Python**
- Build command:

```bash
pip install -r requirements.txt
```

- Start command:

```bash
sh ./scripts/deploy_start.sh
```

You can also use the included `backend/render.yaml` Blueprint.

### 2) Required environment variables on Render

- `DJANGO_SECRET_KEY` = strong random secret
- `JWT_SIGNING_KEY` = long random value (>= 32 chars)
- `DJANGO_DEBUG` = `False`
- `RENDER` = `True`
- `DJANGO_ALLOWED_HOSTS` = your render hostname(s), comma-separated
  - example: `ahju-backend.onrender.com`
- `CORS_ALLOWED_ORIGINS` = your frontend origins, comma-separated
  - example: `https://your-frontend.vercel.app,http://localhost:5173`
- `CSRF_TRUSTED_ORIGINS` = same https frontend origins

Database:
- `DATABASE_URL` (if using Render Postgres)

Firebase (choose one):
- `FIREBASE_CREDENTIALS_JSON` (recommended on Render): full service account JSON string
- OR `FIREBASE_CREDENTIALS` path to mounted credentials file

### 3) Notes

- Static files are served via WhiteNoise in Render mode (`RENDER=True`).
- SSL/cookie security settings auto-enable in Render mode unless overridden.
- On deploy, migrations and collectstatic run automatically from start command.

### 3.1) Auto-seeding products on deploy

This project now supports automatic product seeding during startup via `scripts/deploy_start.sh`.

Environment variables:

- `SEED_PRODUCTS_ON_DEPLOY` (default: `true`)
  - `true` => runs `python manage.py seed_shop_products ...`
  - `false` => skips seeding
- `PRODUCT_IMAGE_SOURCE_DIR` (default: `/opt/render/project/src/media/products`)
  - Folder where product image files are read from during seeding.

Recommended Render values:

```env
SEED_PRODUCTS_ON_DEPLOY=true
PRODUCT_IMAGE_SOURCE_DIR=/opt/render/project/src/media/products
DJANGO_MEDIA_ROOT=/var/data/media
```

Because `seed_shop_products` uses `update_or_create` by slug, running it repeatedly is safe (it updates existing products).

## Deploying backend on Koyeb

Create a **Web Service** on Koyeb from this repository and use:

- Environment: **Python**
- Build command:

```bash
pip install -r requirements.txt
```

- Run command:

```bash
PORT=${PORT:-8000} sh ./scripts/deploy_start.sh
```

### Required environment variables on Koyeb

- `DJANGO_SECRET_KEY` = strong random secret
- `JWT_SIGNING_KEY` = long random value (>= 32 chars)
- `DJANGO_DEBUG` = `False`
- `KOYEB` = `True`
- `DJANGO_ALLOWED_HOSTS` = your koyeb domain(s), comma-separated
  - example: `your-app-your-org.koyeb.app`
- `CORS_ALLOWED_ORIGINS` = frontend origins, comma-separated
- `CSRF_TRUSTED_ORIGINS` = same https frontend origins

Database:
- `DATABASE_URL` (recommended in production)

Firebase:
- `FIREBASE_CREDENTIALS_JSON` (recommended): full service account JSON string
- OR `FIREBASE_CREDENTIALS` path to credentials file

### Koyeb notes

- This project now supports `KOYEB=True` for production security/static defaults.
- WhiteNoise and secure-cookie/SSL settings are enabled automatically when `KOYEB=True` and `DJANGO_DEBUG=False`.
- Ensure your Koyeb domain is included in `DJANGO_ALLOWED_HOSTS`.
- Product auto-seeding on deploy is enabled by default (`SEED_PRODUCTS_ON_DEPLOY=true`).
- Recommended Koyeb env vars for seeding:

```env
SEED_PRODUCTS_ON_DEPLOY=true
PRODUCT_IMAGE_SOURCE_DIR=/app/media/products
```

- If your deployment does not include local image files, seeding still creates/updates products but image copy may warn.

## Supabase Storage setup (for persistent profile/portfolio uploads)

If uploaded user images disappear after redeploy/restart, configure Supabase Storage.

1. In Supabase dashboard, create a **public** bucket (example: `user-media`).
2. In your backend environment variables, set:
   - `SUPABASE_URL=https://your-project-ref.supabase.co`
   - `SUPABASE_STORAGE_BUCKET=user-media`
   - `SUPABASE_SERVICE_ROLE_KEY=...`
3. Redeploy backend.

Notes:

- `SUPABASE_SERVICE_ROLE_KEY` must stay on backend only (never frontend).
- When Supabase env vars are present, upload endpoints use Supabase.
- Existing DB image URLs are not migrated automatically; new uploads use Supabase.
