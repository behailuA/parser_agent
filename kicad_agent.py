import os
import requests
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import sexpdata
import tempfile
from werkzeug.utils import secure_filename

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

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    filename = secure_filename(file.filename)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".kicad_sch", dir="/tmp") as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name
    return jsonify({"file_path": tmp_path})

# Tool schema for OpenAI function calling
parse_kicad_tool = {
    "type": "function",
    "function": {
        "name": "parse_kicad_schematic",
        "description": "Extract components and connections from a KiCad schematic file.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the uploaded KiCad schematic file"
                }
            },
            "required": ["file_path"]
        }
    }
}

def parse_kicad_schematic_tool(file_path):
    try:
        with open(file_path, "r") as f:
            schematic_text = f.read()
        s_expr = sexpdata.loads(schematic_text)
        return parse_kicad_schematic(s_expr)
    except Exception as e:
        return {"error": str(e)}

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        question = data.get("question")
        file_path = data.get("file_path")
        github_url = data.get("github_url")
        temp_file = None
        if not question or (not file_path and not github_url):
            return jsonify({"error": "Missing question and file_path or github_url"}), 400

        # If github_url is provided, download the file to a temp file
        if github_url and not file_path:
            resp = requests.get(github_url)
            if resp.status_code != 200:
                return jsonify({"error": f"Failed to fetch schematic: {resp.status_code}"}), 400
            with tempfile.NamedTemporaryFile(delete=False, suffix=".kicad_sch", dir="/tmp") as tmp:
                tmp.write(resp.content)
                file_path = tmp.name
                temp_file = tmp.name

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": question},
                {"role": "user", "content": f"The file is uploaded at {file_path}"}
            ],
            tools=[parse_kicad_tool],
            tool_choice="auto"
        )

        tool_calls = getattr(response.choices[0].message, "tool_calls", [])
        if tool_calls:
            for tool_call in tool_calls:
                if tool_call.function.name == "parse_kicad_schematic":
                    args = tool_call.function.arguments
                    if isinstance(args, str):
                        import json
                        args = json.loads(args)
                    parsed_data = parse_kicad_schematic_tool(args["file_path"])
                    followup = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "user", "content": question},
                            {"role": "user", "content": f"The file is uploaded at {file_path}"},
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": "parse_kicad_schematic",
                                "content": json.dumps(parsed_data)
                            }
                        ]
                    )
                    answer = followup.choices[0].message.content
                    # Clean up temp file if created
                    if temp_file:
                        try:
                            os.remove(temp_file)
                        except Exception:
                            pass
                    return jsonify({"answer": answer})
        answer = response.choices[0].message.content
        if temp_file:
            try:
                os.remove(temp_file)
            except Exception:
                pass
        return jsonify({"answer": answer})
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

# For Render or local dev: dynamic port
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)