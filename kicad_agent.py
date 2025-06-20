import os
import requests
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import sexpdata

# Use the new OpenAI API client
client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

app = Flask(__name__)
CORS(app)

def parse_kicad_schematic(s_expr):
    """Parse a KiCad schematic S-expression and extract title, components, and nets."""
    title = None
    components = []
    nets = []
    for item in s_expr:
        if isinstance(item, list):
            if item and item[0] == sexpdata.Symbol('title'):
                title = item[1] if len(item) > 1 else None
            elif item and item[0] == sexpdata.Symbol('comp'):
                ref = value = footprint = None
                for sub in item:
                    if isinstance(sub, list):
                        if sub and sub[0] == sexpdata.Symbol('ref'):
                            ref = sub[1]
                        elif sub and sub[0] == sexpdata.Symbol('value'):
                            value = sub[1]
                        elif sub and sub[0] == sexpdata.Symbol('footprint'):
                            footprint = sub[1]
                components.append({
                    "ref": ref,
                    "value": value,
                    "footprint": footprint
                })
            elif item and item[0] == sexpdata.Symbol('net'):
                code = name = None
                for sub in item:
                    if isinstance(sub, list):
                        if sub and sub[0] == sexpdata.Symbol('code'):
                            code = sub[1]
                        elif sub and sub[0] == sexpdata.Symbol('name'):
                            name = sub[1]
                nets.append({
                    "code": code,
                    "name": name
                })
    return {
        "title": title,
        "components": components,
        "nets": nets
    }

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        github_url = data.get("github_url")
        question = data.get("question")
        if not github_url or not question:
            return jsonify({"error": "Missing github_url or question"}), 400

        # Download schematic file from GitHub
        resp = requests.get(github_url)
        if resp.status_code != 200:
            return jsonify({"error": f"Failed to fetch schematic: {resp.status_code}"}), 400
        schematic_text = resp.text

        # Parse schematic S-expression
        try:
            s_expr = sexpdata.loads(schematic_text)
        except Exception as e:
            return jsonify({"error": f"Failed to parse schematic: {str(e)}"}), 400

        summary = parse_kicad_schematic(s_expr)

        # Prepare OpenAI function call
        system_prompt = (
            "You are a helpful assistant for KiCad schematic files. "
            "You are given a summary of the schematic (title, components, nets). "
            "Answer the user's question using this information."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Schematic summary: {summary}\n\nQuestion: {question}"}
        ]

        response = client.chat.completions.create(
            model="gpt-4o",  # or "gpt-3.5-turbo"
            messages=messages,
            temperature=0.2,
            max_tokens=512
        )
        answer = response.choices[0].message.content

        return jsonify({"answer": answer})

    except Exception as e:
        # Log the full traceback for debugging
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

# For Render or local dev: dynamic port
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)