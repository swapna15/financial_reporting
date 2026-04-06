"""
Vercel serverless entry point.

Streamlit cannot run on Vercel (requires persistent WebSocket server).
This handler redirects all traffic to the Railway-hosted Streamlit app.

Set the RAILWAY_APP_URL environment variable in the Vercel project dashboard
to point to your Railway deployment URL.
"""

import os
from http.server import BaseHTTPRequestHandler


RAILWAY_URL = os.environ.get("RAILWAY_APP_URL", "")

_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SAP GL Account Analysis</title>
  <style>
    body { font-family: sans-serif; display: flex; align-items: center;
           justify-content: center; height: 100vh; margin: 0; background: #f5f5f5; }
    .card { background: white; padding: 2rem 3rem; border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,.12); text-align: center; }
    h1 { margin-top: 0; font-size: 1.4rem; }
    p { color: #555; }
    code { background: #eee; padding: 2px 6px; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>SAP GL Account Analysis</h1>
    <p>The app is hosted on Railway. Set <code>RAILWAY_APP_URL</code>
       in your Vercel project settings to enable the redirect.</p>
  </div>
</body>
</html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if RAILWAY_URL:
            self.send_response(302)
            self.send_header("Location", RAILWAY_URL)
            self.end_headers()
        else:
            body = _FALLBACK_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default request logging
