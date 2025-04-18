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

# Configure logging with more detail
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Home route
@app.route('/', methods=['GET'])
def home():
    """Home route to confirm the API is running."""
    logger.debug("Home endpoint accessed")
    return jsonify({
        "message": "Welcome to the Lesson Plan Generator API",
        "status": "running",
        "endpoints": {
            "/": "GET - Home route",
            "/lesson-plan-generation": "POST - Generate and push a lesson plan",
            "/pdf-parse": "POST - Parse PDF and push curriculum data"
        },
        "version": "1.0.0"
    }), 200

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def push_to_curriculum_api(mongo_id, data, authorization_token):
    CURRICULUM_API_URL = f"{BASE_URL}/curriculum/{mongo_id}/units"
    try:
        headers = {
            "Authorization": f"Bearer {authorization_token}",
            "Content-Type": "application/json"
        }
        logger.debug(f"Pushing to curriculum API: {CURRICULUM_API_URL} with data: {data}")
        response = requests.post(CURRICULUM_API_URL, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"Successfully pushed to curriculum API for mongo_id: {mongo_id}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to push to curriculum API: {str(e)}")
        raise

@app.route('/pdf-parse', methods=['POST'])
def pdf_parsing():
    try:
        logger.debug(f"Received PDF parse request: {request.form}")
        
        # Validate file presence and type
        if 'pdf' not in request.files:
            logger.warning("No file part in request")
            return jsonify({"error": "No file uploaded", "status": "failure"}), 400

        file = request.files['pdf']
        if file.filename == '':
            logger.warning("Empty filename received")
            return jsonify({"error": "Empty filename", "status": "failure"}), 400
        
        if not allowed_file(file.filename):
            logger.warning(f"Invalid file type: {file.filename}")
            return jsonify({"error": "Invalid file type. Only PDF allowed", "status": "failure"}), 400

        # Extract form data
        mongo_id = request.form.get('mongo_id')
        authorization_token = request.form.get('authorization_token')
        duration = request.form.get('duration')

        # Validate required form fields
        missing_form_fields = []
        if not mongo_id:
            missing_form_fields.append("mongo_id")
        if not authorization_token:
            missing_form_fields.append("authorization_token")
        if not duration:
            missing_form_fields.append("duration")

        if missing_form_fields:
            logger.warning(f"Missing required form fields: {missing_form_fields}")
            return jsonify({
                "error": "Missing required fields",
                "status": "failure",
                "missing_fields": missing_form_fields
            }), 400

        # Extract and analyze PDF content
        logger.info(f"Processing PDF for mongo_id: {mongo_id}")
        full_text, documents, extracted_metadata = extract_pdf_content(file)
        analysis = analyze_curriculum_text(full_text, mongo_id)
        
        # Ensure analysis is parsed correctly
        try:
            units_data = json.loads(analysis)
            # If units_data is a single dict, wrap it in a list
            if isinstance(units_data, dict):
                units_data = [units_data]
            elif not isinstance(units_data, list):
                raise ValueError("Analysis result must be a JSON object or array")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from analyze_curriculum_text: {str(e)}")
            return jsonify({
                "error": "Invalid curriculum analysis format",
                "status": "failure"
            }), 500

        all_units_data = []
        all_missing_fields = []
        
        for unit_data in units_data:
            if not isinstance(unit_data, dict):
                logger.error(f"Invalid unit_data type: expected dict, got {type(unit_data)}")
                return jsonify({
                    "error": "Invalid unit data format",
                    "status": "failure"
                }), 500
                
            processed_unit = {"status": "Draft"}
            
            def assign_field(field_name, default_value, expected_type, data_key=None):
                key = data_key or field_name
                if key in unit_data and isinstance(unit_data[key], expected_type) and unit_data[key]:
                    processed_unit[field_name] = unit_data[key]
                else:
                    processed_unit[field_name] = default_value

            # Assign all fields with their default values for missing cases
            assign_field("title", "This field is missing", str)
            assign_field("duration", duration, str)
            assign_field("learningObjectives", ["This field is missing"], list)
            assign_field("keyConcepts", ["This field is missing"], list)
            assign_field("standards", [{"code": "N/A", "description": "This field is missing"}], list)
            assign_field("assessments", [{"type": "N/A", "criteria": "This field is missing"}], list)
            assign_field("materials", [{"externalLinks": [], "description": "This field is missing"}], list)
            assign_field("tools", ["This field is missing"], list)

            # Identify missing fields
            missing_fields = [
                field for field, value in processed_unit.items() 
                if field != "status" and (
                    (isinstance(value, str) and value == "This field is missing") or
                    (isinstance(value, list) and value in [["This field is missing"],
                                                          [{"code": "N/A", "description": "This field is missing"}],
                                                          [{"type": "N/A", "criteria": "This field is missing"}],
                                                          [{"externalLinks": [], "description": "This field is missing"}]])
                )
            ]
            
            if missing_fields:
                all_missing_fields.extend([f"{processed_unit.get('title', 'Unnamed Unit')}: {field}" for field in missing_fields])
            
            all_units_data.append(processed_unit)

        # If there are any missing fields, return 400 with the list
        if all_missing_fields:
            logger.warning(f"Missing required fields in PDF data: {all_missing_fields}")
            return jsonify({
                "error": "Incomplete curriculum data",
                "status": "failure",
                "missing_fields": all_missing_fields
            }), 400

        # If we reach here, all required data is present
        # Push all units to API
        api_responses = []
        for unit_data in all_units_data:
            api_response = push_to_curriculum_api(mongo_id, unit_data, authorization_token)
            api_responses.append(api_response)

        logger.info(f"PDF parsed and all data pushed successfully for mongo_id: {mongo_id}")
        return jsonify({
            "api_responses": api_responses,
            "status": "success",
            "message": "All curriculum data processed and pushed successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error in pdf-parse: {str(e)}", exc_info=True)
        return jsonify({
            "error": str(e),
            "status": "failure",
            "details": "An unexpected error occurred while processing your request"
        }), 500

def push_to_api(title, curriculum_id, status, lesson_plan, authorization_token):
    temp_md_path = os.path.join(app.config['UPLOAD_FOLDER'], f"lesson_plan_{curriculum_id}.md")
    API_URL = f"{BASE_URL}/lessons"
    try:
        logger.debug(f"Writing lesson plan to {temp_md_path}")
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
            logger.debug(f"Pushing to API: {API_URL} with data: {data}")
            response = requests.post(API_URL, headers=headers, data=data, files=files)
            response.raise_for_status()
            
            response_data = response.json()
            lesson_id = response_data.get("_id")  # Assuming API returns the inserted document with _id
            logger.info(f"Successfully pushed lesson plan to API. Lesson ID: {lesson_id}")
            return lesson_id  # Return the lesson plan's ObjectId

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to push to API: {str(e)}")
        raise
    finally:
        if os.path.exists(temp_md_path):
            os.remove(temp_md_path)
            logger.debug(f"Cleaned up temporary file: {temp_md_path}")

@app.route('/lesson-plan-generation', methods=['POST'])
def lesson_plan_generator():
    try:
        # Log incoming request
        logger.debug(f"Received request: {request.form}")
        
        # Extract form data
        mongo_id = request.form.get('mongo_id')
        authorization_token = request.form.get('authorization_token')
        unit_id = request.form.get('unit_id')

        # Validate inputs
        if not mongo_id:
            logger.warning("Missing mongo_id in request")
            return jsonify({"error": "Missing mongo_id", "status": "failure"}), 400
        if not authorization_token:
            logger.warning("Missing authorization_token in request")
            return jsonify({"error": "Missing authorization_token", "status": "failure"}), 400
        if not unit_id:
            logger.warning("Missing unit_id in request")
            return jsonify({"error": "Missing unit_id", "status": "failure"}), 400

        # Generate lesson plan
        logger.info(f"Generating lesson plan for mongo_id: {mongo_id}")
        lesson_plan, context_text = generate_lesson_plan(mongo_id)
        
        title = context_text["title"]
        curriculum_id = mongo_id
        status = "Draft"

        # Push to API and get lesson ID
        lesson_id = push_to_api(title, curriculum_id, status, lesson_plan, authorization_token)

        # Ensure lesson_id is a string (MongoDB ObjectId format)
        if not lesson_id:
            logger.error("No lesson_id returned from push_to_api")
            raise Exception("Failed to retrieve lesson plan ObjectId from API")
        
        logger.info(f"Lesson plan generated and pushed successfully for {mongo_id}. Lesson ID: {lesson_id}")
        return jsonify({
            "_id": str(lesson_id),  
            "lesson_plan": lesson_plan,
            "status": "success",
            "message": "Lesson plan generated and pushed successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error in lesson-plan-generation: {str(e)}", exc_info=True)
        return jsonify({
            "error": str(e),
            "status": "failure",
            "details": "An unexpected error occurred while processing your request"
        }), 500

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    logger.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5002, debug=True)