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
