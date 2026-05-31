"""
Terms Guard AI - web demo server
=================================
Reuses the original, tested backend (app.py) without changes. It only adds a
homepage that serves the paste-and-analyze page, and runs on the port that
Hugging Face Spaces expects (7860).

The /process and /health routes come straight from app.py, so the analysis
logic is identical to the browser extension.
"""

import os
from flask import send_from_directory

# Importing app.py loads the models once and registers /process and /health.
from app import app

HERE = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def home():
    return send_from_directory(HERE, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, threaded=True)
