# ZTERA TeraBox API (Vercel-ready, no Chromium, no ffmpeg)

Lightweight TeraBox metadata + signed M3U8 streaming-link extractor. Sirf HTTP
requests use karta hai — koi headless browser ya ffmpeg dependency nahi, isliye
Vercel ke serverless Python functions pe directly chalta hai.

## Endpoints

### `GET /api/info?url=<terabox_share_url>`

Response:
```json
{
  "success": true,
  "filename": "movie.mp4",
  "size": 524288000,
  "size_mb": 500.0,
  "thumb": "https://...",
  "fs_id": 123456789,
  "surl": "AbCdEfGh",
  "streaming_urls": {
    "M3U8_AUTO_1080": "https://dm.1024tera.com/share/streaming?...",
    "M3U8_AUTO_720": "...",
    "M3U8_AUTO_480": "...",
    "M3U8_AUTO_360": "..."
  }
}
```

`streaming_urls` mein jo links hain unhe apne frontend player mein **HLS.js**
se directly consume karo (ya pehle apne Cloudflare Worker se proxy karke CORS
fix karo, jaisa tumhara existing setup hai).

### `GET /api/health`

Basic uptime check — `{"status": "ok"}`.

## Deploy steps

1. Is folder ko GitHub repo mein push karo.
2. [vercel.com](https://vercel.com) pe "New Project" → apna repo import karo.
   `vercel.json` already configured hai, kuch extra setting nahi chahiye.
3. Project → Settings → Environment Variables mein `COOKIES1`, `COOKIES2`,
   etc. add karo (`.env.example` dekho — format browser se copy kiya hua
   `Cookie:` header hai).
4. Deploy. `https://<your-project>.vercel.app/api/info?url=...` test karo.

## Important notes

- **Cookies expire** — TeraBox session cookies kuch din/hafton mein expire ho
  sakte hain. Agar `/api/info` errors dene lage (`errno` mismatch ya 502), to
  fresh cookies extract karke env variables update karo aur redeploy karo.
- **Ye sirf links deta hai, file download/remux nahi karta** — heavy chunk
  collection aur ffmpeg merge Vercel serverless ke liye suited nahi hai
  (timeout limits). Agar future mein full `.mp4` server-side bana ke serve
  karna ho, to wo part ek alag VPS (Hostinger/Hetzner) pe deploy karna padega.
- Production mein `*` wala CORS (`Access-Control-Allow-Origin`) apni website
  ke actual domain se replace kar dena `api/index.py` mein.
