import os
import json
import tempfile
from typing import List, Dict, Tuple, Any
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain.schema import Document
import fitz
import base64
import google.generativeai as genai
from pdf2image import convert_from_path
import pytesseract
import re
from mongo import get_lesson_data
from bson.objectid import ObjectId


# Load environment variables from .env file
load_dotenv()

# Initialize Google Generative AI client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME")
genai.configure(api_key=GEMINI_API_KEY) 


class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)


def extract_images_from_pdf(pdf_path: str) -> List[bytes]:
    pdf_document = fitz.open(pdf_path)
    images = []
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        image_list = page.get_images(full=True)
        for img in image_list:
            xref = img[0]
            base_image = pdf_document.extract_image(xref)
            images.append(base_image["image"])
    pdf_document.close()
    return images


def encode_image_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def perform_ocr_on_pdf(pdf_path: str) -> List[str]:
    try:
        pages = convert_from_path(pdf_path, dpi=300)
        return [pytesseract.image_to_string(page, lang='eng').strip() for page in pages]
    except Exception as e:
        raise Exception(f"Error performing OCR with Tesseract: {str(e)}")


def extract_pdf_content(pdf_file: Any) -> Tuple[str, List[Document], Dict[str, Any]]:
    temp_file_path = None
    metadata = {"images": [], "page_count": 0}

    try:
        # Save uploaded file to a temporary location
        pdf_content = pdf_file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(pdf_content)
            temp_file_path = temp_file.name

        # Open PDF with PyMuPDF for metadata and images
        pdf_document = fitz.open(temp_file_path)
        metadata["page_count"] = len(pdf_document)
        metadata["images"] = [encode_image_to_base64(img) for img in extract_images_from_pdf(temp_file_path)]

        # Extract text using PyPDFLoader
        loader = PyPDFLoader(temp_file_path)
        documents = loader.load()
        processed_documents = []

        for i, doc in enumerate(documents):
            text = doc.page_content.strip()
            page_metadata = {"page": i + 1, "source": ""}

            # Fallback logic if PyPDFLoader text is insufficient
            if len(text) < 10:
                try:
                    images = convert_from_path(temp_file_path, dpi=300, first_page=i + 1, last_page=i + 1)
                    if images:
                        text = pytesseract.image_to_string(images[0], lang="eng").strip()
                        page_metadata["source"] = "ocr"
                    else:
                        page = pdf_document.load_page(i)
                        image_list = page.get_images(full=True)
                        if image_list:
                            xref = image_list[0][0]
                            base_image = pdf_document.extract_image(xref)
                            text = pytesseract.image_to_string(base_image["image"], lang="eng").strip()
                            page_metadata["source"] = "pymupdf_ocr"
                        else:
                            text = ""
                            page_metadata["source"] = "error"
                except Exception as e:
                    print(f"Error processing page {i + 1} with OCR: {str(e)}")
                    text = ""
                    page_metadata["source"] = "error"
            else:
                page_metadata["source"] = "pypdf"

            # Clean text
            if not text:
                text = "No extractable content found on this page"
            else:
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'[|lI]{2,}', 'l', text)

            processed_documents.append(Document(page_content=text, metadata=page_metadata))

        full_text = "\n\n--- Page Break ---\n\n".join(doc.page_content for doc in processed_documents)
        return full_text, processed_documents, metadata

    except Exception as e:
        raise Exception(f"Error extracting PDF content: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


def analyze_curriculum_text(text: str) -> str:
    prompt = f"""
    You are an expert curriculum analyzer. Extract detailed information from the given curriculum document and return it in the following JSON structure only:

    {{
        "title": "Unit title",
        "duration": "Duration",
        "learningObjectives": ["List of learning objectives"],
        "keyConcepts": ["List of key topics and concepts"],
        "standards": [{{"code": "Standard code", "description": "Description of the standard"}}],
        "assessments": [{{"type": "Type of assessment", "criteria": "Assessment criteria"}}],
        "materials": [{{"externalLinks": ["Array of external resource URLs"], "description": "Description of resources"}}],
        "tools": ["List of tools required"]
    }}

    Instructions:
    1. Extract ONLY information explicitly present in the document
    2. Return data in the exact JSON structure shown above
    3. Include ONLY fields with available information (omit entire fields/keys if data is missing)
    4. Do NOT add placeholder text like "Not specified" or empty arrays/objects
    5. Return ONLY the JSON object without any additional text or formatting

    Document text:
    {text}
    """
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = model.generate_content(prompt)
        result = response.text.replace('```json', '').replace('```', '').strip()
        json.loads(result)  # Validate JSON
        return result
    except json.JSONDecodeError as e:
        fallback = json.dumps({"title": "Unknown", "learningObjectives": ["Learn basics"], "keyConcepts": ["Basics"]})
        print(f"Invalid JSON response: {str(e)}. Returning fallback: {fallback}")
        return fallback
    except Exception as e:
        raise Exception(f"Error analyzing curriculum text: {str(e)}")


def generate_lesson_plan(mongo_id: str) -> Tuple[str, Dict[str, Any]]:
    try:
        # Validate mongo_id
        if not ObjectId.is_valid(mongo_id):
            raise ValueError(f"Invalid MongoDB ObjectID: {mongo_id}")

        # Fetch data from MongoDB
        mongo_data = get_lesson_data(mongo_id)
        if not mongo_data:
            raise ValueError(f"No data found for MongoDB ID: {mongo_id}")

        # Extract unit and curriculum details
        unit = mongo_data.get("units", [{}])[0]
        topic = unit.get("title", "Untitled Lesson")
        grade = mongo_data.get("gradeId", ["Unknown Grade"])[0]
        duration_str = unit.get("duration", "1 weeks")
        days = int(duration_str.split()[0]) if duration_str.split()[0].isdigit() else 1
        country = mongo_data.get("countryId", [{}])[0].get("name", "Unknown Country")

        # Prepare context for prompts
        context = {
            "title": topic,
            "duration": f"{days} days",
            "learningObjectives": unit.get("learningObjectives", ["Understand basic concepts"]),
            "keyConcepts": unit.get("keyConcepts", [topic]),
            "standards": unit.get("standards", []),
            "assessments": unit.get("assessments", []),
            "materials": unit.get("materials", []),
            "tools": unit.get("tools", [])
        }
        context_json = json.dumps(context, cls=MongoJSONEncoder)

        purpose = _generate_section(
            "Purpose",
            f"Generate the Purpose section for a {days}-day lesson plan on '{topic}' for {grade} students, following {country} curriculum standards.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- Overview with {country} context\n"
            f"- 3-5 {country} curriculum standards (use codes: {', '.join([s['code'] for s in context['standards']])})\n"
            f"- Real-world {country} applications\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        objectives = _generate_section(
            "Objectives",
            f"Generate the Objectives section for a {days}-day lesson plan on '{topic}' for {grade} students, following {country} curriculum standards.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- 4-6 measurable objectives based on {json.dumps(context['learningObjectives'], cls=MongoJSONEncoder)}\n"
            f"- Activities and assessments from {json.dumps(context['assessments'], cls=MongoJSONEncoder)}\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        planning = _generate_section(
            "Planning & Preparation",
            f"Generate the Planning & Preparation section for a {days}-day lesson plan on '{topic}' for {grade} students.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- Materials: {json.dumps(context['materials'], cls=MongoJSONEncoder)}\n"
            f"- Tools: {json.dumps(context['tools'])}\n"
            f"- Challenges and solutions\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        prior_knowledge = _generate_section(
            "Prior Knowledge",
            f"Generate the Prior Knowledge section for a {days}-day lesson plan on '{topic}' for {grade} students.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- Prerequisites based on {topic}\n"
            f"- Diagnostic methods\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        lesson_flow = ""
        for day in range(1, days + 1):
            daily_content = _generate_section(
                f"Day {day}",
                f"Generate a detailed lesson plan for Day {day} of a {days}-day lesson plan on '{topic}' for {grade} students, following {country} curriculum standards.\n"
                f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
                f"Include sections:\n"
                f"### Introduction\n### Mini-Lesson\n### Guided Practice\n### Independent Practice\n### Assessment\n### Wrap-Up\n"
                f"Include {country}-specific examples and 50-minute timing breakdown.\n"
                f"Use materials: {json.dumps(context['materials'], cls=MongoJSONEncoder)} and tools: {json.dumps(context['tools'])}\n"
                f"Return only the content under these headings.\n"
                f"Context: {context_json}"
            )
            lesson_flow += f"## Day {day}\n{daily_content}\n\n"
        lesson_flow = lesson_flow.strip()

        extension = _generate_section(
            "Extension/Enrichment",
            f"Generate the Extension/Enrichment section for a {days}-day lesson plan on '{topic}' for {grade} students.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- Cross-curricular projects linking to {json.dumps(context['keyConcepts'])}\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        assessment_tools = _generate_section(
            "Assessment Tools",
            f"Generate the Assessment Tools section for a {days}-day lesson plan on '{topic}' for {grade} students.\n"
            f"Use Markdown format with #, ##, ### headings only, no ``` marks.\n"
            f"- Comprehensive assessments based on {json.dumps(context['assessments'], cls=MongoJSONEncoder)}\n"
            f"Return only the content under this section.\n"
            f"Context: {context_json}"
        )

        # Compile full lesson plan
        full_lesson_plan = f"""
{topic} Lesson Plan
## 1. Purpose
{purpose}

## 2. Objectives
{objectives}

## 3. Planning & Preparation
{planning}

## 4. Prior Knowledge
{prior_knowledge}

## 5. Lesson Flow
{lesson_flow}

## 6. Extension/Enrichment
{extension}

## 7. Assessment Tools
{assessment_tools}
        """.strip()

        return full_lesson_plan, context

    except Exception as e:
        raise Exception(f"Failed to generate lesson plan: {str(e)}")


def _generate_section(section_name: str, prompt: str) -> str:
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        raise Exception(f"Error generating {section_name} section: {str(e)}")

