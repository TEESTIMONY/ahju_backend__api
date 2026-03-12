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
python manage.py migrate && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
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
