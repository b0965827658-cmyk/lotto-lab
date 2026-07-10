# Lotto Lab Deployment

Lotto Lab is a Python standard-library web app. It serves the frontend from `public/` and exposes APIs from `server.py`.

## Quick Local Run

```bash
python server.py
```

Open:

```text
http://127.0.0.1:8787/?v=10
```

## Production Environment Variables

- `PORT`: Set by most cloud hosts. Defaults to `8787`.
- `HOST`: Defaults to `0.0.0.0`.
- `LOTTO_STRIPE_PAYMENT_LINK`: Optional Stripe Payment Link for the Pro subscription button.
- `LOTTO_CACHE_TTL_SECONDS`: API data cache seconds. Defaults to `300` so new draws update faster without recalculating on every request.
- `LOTTO_VAPID_PUBLIC_KEY`: Web Push public key for real device push notifications.
- `LOTTO_VAPID_PRIVATE_KEY`: Web Push private key for real device push notifications.
- `LOTTO_PUSH_CONTACT_EMAIL`: Contact email used in the push sender claim.
- `LOTTO_NOTIFY_SECRET`: Secret required by `/api/notify-latest` before it broadcasts notifications.
- `LOTTO_SUBSCRIPTIONS_FILE`: Optional path for saved push subscriptions. Defaults to `data/push_subscriptions.json`.
- `DATABASE_URL`: Optional PostgreSQL connection URL. When present, the app stores draw history, notification subscriptions, saved picks, and notification state in PostgreSQL.
- `LOTTO_SQLITE_PATH`: Optional local SQLite path. Defaults to `data/lotto.sqlite3` and is used automatically when `DATABASE_URL` is not set.

## Persistent Storage

The app now initializes its database automatically. On your Mac, it uses SQLite and keeps the database at `data/lotto.sqlite3`, so your personal history and saved settings remain after restarting the app. Existing JSON notification files are migrated into SQLite on first start.

For a permanent hosted database, create or connect a PostgreSQL database in Render and add its internal connection URL to the web service as `DATABASE_URL`. The app will switch to PostgreSQL on the next deploy without changing the frontend. If no hosted database is connected, Render's default service filesystem should be treated as temporary, so local SQLite is the reliable self-use option until that connection is made.

Generate VAPID keys locally after installing the requirements:

```bash
python - <<'PY'
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
public_numbers = private_key.public_key().public_numbers()
raw_public = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")

print("LOTTO_VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode())
print("LOTTO_VAPID_PRIVATE_KEY=" + private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode().replace("\n", "\\n"))
PY
```

For reliable production notifications, create a Render Cron Job or external scheduler that calls:

```text
POST https://your-domain.example/api/notify-latest
X-Lotto-Notify-Secret: your-secret
Content-Type: application/json

{"game":"tw539"}
```

Run another scheduled call for `{"game":"ca-fantasy5"}` if you want California Fantasy 5 notifications too.

The app can show local notifications while the user keeps the site open. Full background push after the user closes the app requires the VAPID variables above plus a scheduled trigger.

## Health Check

```text
/api/health
```

## Deploy To Render

1. Push this folder to a GitHub repository.
2. In Render, create a new Web Service from the repository.
3. Use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python server.py`
   - Health check path: `/api/health`
4. Add `LOTTO_STRIPE_PAYMENT_LINK` only when you have a real Stripe Payment Link.
5. Open the generated HTTPS URL.

The included `render.yaml` can also be used as a Render Blueprint.

## Deploy With Docker

```bash
docker build -t lotto-lab .
docker run --rm -p 8787:8787 lotto-lab
```

Open:

```text
http://127.0.0.1:8787/?v=10
```

## Custom Domain

After the cloud host gives you a public HTTPS URL, add your domain in that host's dashboard and point DNS to the host as instructed.

## Notes

- Current cross-year search is implemented for 今彩 539 through Taiwan open-data yearly zip files.
- 加州天天樂 still depends on the current page source and should get a stronger historical data source before being sold as a full cross-year product.
- The recommendation model is a statistical tracking tool, not a guarantee or prediction of winning numbers.
