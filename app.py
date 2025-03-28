from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json
import requests
import logging
import os
from dotenv import load_dotenv
from functions import extract_pdf_content, analyze_curriculum_text, generate_lesson_plan

app = Flask(__name__)
CORS(app)
app.config['UPLOAD_FOLDER'] = './uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf'}

load_dotenv()

BASE_URL = os.getenv("BASE_URL")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Home route
@app.route('/', methods=['GET'])
def home():
    """Home route to confirm the API is running."""
    return jsonify({
        "message": "Welcome to the Lesson Plan Generator API",
        "status": "running",
        "endpoints": {
            "/": "GET - Home route",
            "/lesson-plan-generation": "POST - Generate and push a lesson plan",
            "/pdf-parse": "POST - Parse PDF and push curriculum data"
        },
        "version": "1.0.0"  # You can update this as needed
    }), 200

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def push_to_api(title, curriculum_id, status, lesson_plan, authorization_token):
    temp_md_path = os.path.join(app.config['UPLOAD_FOLDER'], f"lesson_plan_{curriculum_id}.md")
    API_URL = f"{BASE_URL}/lessons"
    try:
        with open(temp_md_path, 'w', encoding='utf-8') as md_file:
            md_file.write(lesson_plan)

        data = {
            "title": title,
            "curriculumId": curriculum_id,
            "status": status
        }
        
        with open(temp_md_path, 'rb') as md_file:
            files = {
                "mdFile": (os.path.basename(temp_md_path), md_file, "text/markdown")
            }
            headers = {"Authorization": f"Bearer {authorization_token}"}
            response = requests.post(API_URL, headers=headers, data=data, files=files)
            response.raise_for_status()
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to push to API: {str(e)}")
        raise
    finally:
        if os.path.exists(temp_md_path):
            os.remove(temp_md_path)

@app.route('/lesson-plan-generation', methods=['POST'])
def Lesson_Plan_Generator():
    try:
        mongo_id = request.form.get('mongo_id')
        authorization_token = request.form.get('authorization_token')
        unit_id = request.form.get('unit_id')

        if not mongo_id:
            return Response('{"error": "Missing mongo_id"}', status=400, mimetype='application/json')
        if not authorization_token:
            return Response('{"error": "Missing authorization_token"}', status=400, mimetype='application/json')
        if not unit_id:
            return Response('{"error": "Missing unit_id"}', status=400, mimetype='application/json')

        lesson_plan, context_text = generate_lesson_plan(mongo_id)
        
        title = context_text["title"]
        curriculum_id = mongo_id
        status = "Draft"

        push_to_api(title, curriculum_id, status, lesson_plan, authorization_token)

        return Response(lesson_plan.encode('utf-8'), mimetype='text/plain'), 200

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return Response(f'{{"error": "{str(e)}"}}', status=500, mimetype='application/json')

def push_to_curriculum_api(mongo_id, data, authorization_token):
    CURRICULUM_API_URL = f"{BASE_URL}/curriculum/{mongo_id}/units"
    try:
        headers = {
            "Authorization": f"Bearer {authorization_token}",
            "Content-Type": "application/json"
        }
        response = requests.post(CURRICULUM_API_URL, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to push to curriculum API: {str(e)}")
        raise

@app.route('/pdf-parse', methods=['POST'])
def Pdf_Parsing():
    try:
        if 'pdf' not in request.files:
            logger.error("No file part in request")
            return Response('{"error": "No file uploaded"}', status=400, mimetype='application/json')

        file = request.files['pdf']
        if file.filename == '':
            logger.error("Empty filename received")
            return Response('{"error": "Empty filename"}', status=400, mimetype='application/json')
            
        if not allowed_file(file.filename):
            return Response('{"error": "Invalid file type"}', status=400, mimetype='application/json')

        mongo_id = request.form.get('mongo_id')
        authorization_token = request.form.get('authorization_token')

        if not mongo_id:
            logger.error("Missing mongo_id")
            return Response('{"error": "Missing mongo_id"}', status=400, mimetype='application/json')
        if not authorization_token:
            logger.error("Missing authorization_token")
            return Response('{"error": "Missing authorization_token"}', status=400, mimetype='application/json')

        full_text, documents, extracted_metadata = extract_pdf_content(file)
        analysis = analyze_curriculum_text(full_text)
        data = json.loads(analysis)

        unit_data = {"status": "Draft"}

        if "title" in data and isinstance(data["title"], str) and data["title"]:
            unit_data["title"] = data["title"]
        else:
            unit_data["title"] = "This field is missing"

        if "duration" in data and isinstance(data["duration"], str) and data["duration"]:
            unit_data["duration"] = data["duration"]
        else:
            unit_data["duration"] = "This field is missing"

        if "learningObjectives" in data and isinstance(data["learningObjectives"], list) and data["learningObjectives"]:
            unit_data["learningObjectives"] = data["learningObjectives"]
        else:
            unit_data["learningObjectives"] = ["This field is missing"]

        if "keyConcepts" in data and isinstance(data["keyConcepts"], list) and data["keyConcepts"]:
            unit_data["keyConcepts"] = data["keyConcepts"]
        else:
            unit_data["keyConcepts"] = ["This field is missing"]

        if "standards" in data and isinstance(data["standards"], list) and data["standards"]:
            unit_data["standards"] = data["standards"]
        else:
            unit_data["standards"] = [{"code": "N/A", "description": "This field is missing"}]

        if "assessments" in data and isinstance(data["assessments"], list) and data["assessments"]:
            unit_data["assessments"] = data["assessments"]
        else:
            unit_data["assessments"] = [{"type": "N/A", "criteria": "This field is missing"}]

        if "materials" in data and isinstance(data["materials"], list) and data["materials"]:
            unit_data["materials"] = data["materials"]
        else:
            unit_data["materials"] = [{"externalLinks": [], "description": "This field is missing"}]

        if "tools" in data and isinstance(data["tools"], list) and data["tools"]:
            unit_data["tools"] = data["tools"]
        else:
            unit_data["tools"] = ["This field is missing"]

        missing_fields = []
        for field, value in unit_data.items():
            if field == "status":
                continue
            if isinstance(value, str) and value == "This field is missing":
                missing_fields.append(field)
            elif isinstance(value, list) and value == ["This field is missing"]:
                missing_fields.append(field)
            elif field == "standards" and value == [{"code": "N/A", "description": "This field is missing"}]:
                missing_fields.append(field)
            elif field == "assessments" and value == [{"type": "N/A", "criteria": "This field is missing"}]:
                missing_fields.append(field)
            elif field == "materials" and value == [{"externalLinks": [], "description": "This field is missing"}]:
                missing_fields.append(field)

        api_response = push_to_curriculum_api(mongo_id, unit_data, authorization_token)

        response_data = {"api_response": api_response}
        if missing_fields:
            response_data["warning"] = f"Data pushed, but the following fields are missing or invalid: {', '.join(missing_fields)}"
        else:
            response_data["message"] = "Data pushed successfully with all fields present"

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return Response(f'{{"error": "{str(e)}"}}', status=500, mimetype='application/json')

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=5002, debug=False)  