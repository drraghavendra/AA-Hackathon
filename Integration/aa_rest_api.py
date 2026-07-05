"""
Minimal REST wrapper around aa_agentic_pipeline.py, for Automation Anywhere bots
that integrate via a REST Web Service call action rather than a local Python
script action (more portable across AA Cloud / Control Room setups where the
bot runner may not have Python + pandas installed locally).

Run locally to test:
    pip install flask pandas openpyxl --break-system-packages
    python3 aa_rest_api.py
    # then, in another terminal:
    curl -X POST http://localhost:5000/run-plan \
         -F "dataset=@Akshaya_Patra_Dataset.xlsx"

In an AA bot: use the REST Web Service (POST, multipart/form-data) action
pointing at wherever this is deployed, with the dataset file (or a fresh
export of it from your ERP/inventory system) as the "dataset" form field.
The response body is the same JSON produced by day_plan_output.json.

NOTE: this is a bare-bones dev server (Flask's built-in run()), fine for a
hackathon demo. For anything resembling production, put it behind a real
WSGI server and add auth — Automation Anywhere's own docs will have current
guidance on securely calling external REST endpoints from a bot.
"""

import json
import os
import tempfile

from flask import Flask, request, Response, jsonify

from aa_agentic_pipeline import run

app = Flask(__name__)


@app.route("/run-plan", methods=["POST"])
def run_plan():
    if "dataset" not in request.files:
        return jsonify({"error": "multipart form field 'dataset' (the .xlsx file) is required"}), 400

    file = request.files["dataset"]
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = run(tmp_path)
        # pandas/numpy scalar types (bool_, int64, etc.) aren't natively
        # JSON-serialisable - fall back to str() for anything json can't
        # handle directly, matching the file-writer's behaviour.
        return Response(json.dumps(result, default=str), mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
