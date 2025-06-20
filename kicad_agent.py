import os
import tempfile
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import requests
import sexpdata  # pip install sexpdata

# --- KiCad Schematic Parser Implementation ---
def parse_kicad_schematic(file_path):
    """
    Parses a KiCad .kicad_sch file and extracts components and nets.
    Returns a dict with 'components' and 'nets'.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        sexp = sexpdata.loads(content)
    except Exception as e:
        return {"error": f"Failed to parse schematic: {e}"}

    components = []
    nets = []

    def find_items(sexp, key):
        """Recursively find all lists starting with key."""
        found = []
        if isinstance(sexp, list):
            if sexp and isinstance(sexp[0], sexpdata.Symbol) and sexp[0].value() == key:
                found.append(sexp)
            for item in sexp:
                found.extend(find_items(item, key))
        return found

    # Extract components (symbols)
    for symbol in find_items(sexp, "symbol"):
        ref = None
        value = None
        lib_id = None
        at = None
        for item in symbol[1:]:
            if isinstance(item, list) and item:
                if item[0] == sexpdata.Symbol("reference"):
                    ref = item[1] if len(item) > 1 else None
                elif item[0] == sexpdata.Symbol("value"):
                    value = item[1] if len(item) > 1 else None
                elif item[0] == sexpdata.Symbol("lib_id"):
                    lib_id = item[1] if len(item) > 1 else None
                elif item[0] == sexpdata.Symbol("at"):
                    at = [float(x) for x in item[1:3]] if len(item) > 2 else None
        components.append({
            "reference": ref,
            "value": value,
            "lib_id": lib_id,
            "position": at
        })

    # Extract nets
    for net in find_items(sexp, "net"):
        net_name = None
        nodes = []
        for item in net[1:]:
            if isinstance(item, list) and item:
                if item[0] == sexpdata.Symbol("name"):
                    net_name = item[1] if len(item) > 1 else None
                elif item[0] == sexpdata.Symbol("node"):
                    node_ref = None
                    node_pin = None
                    for nitem in item[1:]:
                        if isinstance(nitem, list) and nitem:
                            if nitem[0] == sexpdata.Symbol("ref"):
                                node_ref = nitem[1]
                            elif nitem[0] == sexpdata.Symbol("pin"):
                                node_pin = nitem[1]
                    if node_ref and node_pin:
                        nodes.append({"ref": node_ref, "pin": node_pin})
        nets.append({
            "name": net_name,
            "connections": nodes
        })

    return {
        "components": components,
        "nets": nets
    }

def parse_kicad_schematic_from_github(github_url):
    """
    Downloads a KiCad schematic from a GitHub raw URL and parses it.
    """
    response = requests.get(github_url)
    if response.status_code != 200:
        return {"error": f"Failed to download schematic from {github_url}"}
    content = response.text
    # Save to a temp file for parsing
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sch") as temp:
        temp.write(content.encode("utf-8"))
        temp_path = temp.name
    result = parse_kicad_schematic(temp_path)
    os.remove(temp_path)
    return result

# --- OpenAI setup ---
openai.api_key = os.environ["OPENAI_API_KEY"]

# --- Function schema for OpenAI ---
function_schema = {
    "name": "parse_kicad_schematic",
    "description": "Extract components and connections from a KiCad schematic file from a GitHub URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "github_url": {
                "type": "string",
                "description": "The raw GitHub URL to the KiCad schematic file"
            }
        },
        "required": ["github_url"]
    }
}

# --- Flask app ---
app = Flask(__name__)
CORS(app)  # Enable CORS for all domains

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data["message"]
    github_url = data["github_url"]

    # Step 1: Initial call with tool enabled
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": user_message},
            {"role": "user", "content": f"The schematic is at {github_url}"}
        ],
        tools=[{
            "type": "function",
            "function": function_schema
        }],
        tool_choice="auto"
    )

    # Step 2: If tool call is requested, run your parser and continue
    tool_calls = response.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
    if tool_calls:
        for tool_call in tool_calls:
            if tool_call["function"]["name"] == "parse_kicad_schematic":
                arguments = json.loads(tool_call["function"]["arguments"])
                parsed_data = parse_kicad_schematic_from_github(arguments["github_url"])
                # Step 3: Continue the conversation with the tool output
                followup = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "user", "content": user_message},
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": "parse_kicad_schematic",
                            "content": json.dumps(parsed_data)
                        }
                    ]
                )
                return jsonify({"response": followup["choices"][0]["message"]["content"]})
    else:
        return jsonify({"response": response["choices"][0]["message"]["content"]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)