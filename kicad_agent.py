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

def find_all(symbol, s_expr):
    """Recursively find all lists starting with the given symbol."""
    found = []
    if isinstance(s_expr, list):
        if s_expr and isinstance(s_expr[0], sexpdata.Symbol) and s_expr[0].value() == symbol:
            found.append(s_expr)
        for item in s_expr:
            found.extend(find_all(symbol, item))
    return found

def parse_kicad_schematic(s_expr):
    """Parse a KiCad schematic S-expression and extract title, components, and nets."""
    # Find title
    title = None
    title_blocks = find_all('title_block', s_expr)
    if title_blocks:
        for item in title_blocks[0]:
            if isinstance(item, list) and item and item[0] == sexpdata.Symbol('title'):
                title = item[1]
                break

    # Find all components and nets recursively
    components = []
    nets = []

    # For components, look for 'symbol' or 'comp'
    for comp in find_all('symbol', s_expr) + find_all('comp', s_expr):
        ref = value = footprint = None
        for sub in comp:
            if isinstance(sub, list):
                if sub and sub[0] == sexpdata.Symbol('property'):
                    if len(sub) > 2 and sub[1] == "Reference":
                        ref = sub[2]
                    elif len(sub) > 2 and sub[1] == "Value":
                        value = sub[2]
                    elif len(sub) > 2 and sub[1] == "Footprint":
                        footprint = sub[2]
        components.append({
            "ref": ref,
            "value": value,
            "footprint": footprint
        })

    # For nets, look for 'net'
    for net in find_all('net', s_expr):
        code = name = None
        for sub in net:
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