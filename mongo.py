from pymongo import MongoClient
import certifi
import os
from dotenv import load_dotenv
from datetime import datetime
from bson.objectid import ObjectId
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# MongoDB configuration
mongo_uri = os.getenv("MONGO_URI")
db_name = os.getenv("DATABASE_NAME")
collection_name = os.getenv("COLLECTION_NAME")

def get_mongodb_connection():
    try:
        mongo_client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
        mongo_client.admin.command('ping')  # Test connection
        # logger.info(f"Connected to MongoDB at {mongo_uri}")
        return mongo_client
    except Exception as e:
        raise Exception(f"MongoDB connection failed: {str(e)}")

def mongodb_operation(operation_func):
    def wrapper(*args, **kwargs):
        mongo_client = None
        try:
            mongo_client = get_mongodb_connection()
            db = mongo_client.get_database(db_name)
            collection = db.get_collection(collection_name)
            # logger.info(f"Using database: {db_name}, collection: {collection_name}, document count: {collection.count_documents({})}")
            return operation_func(collection, *args, **kwargs)
        except Exception as e:
            error_msg = f"MongoDB operation failed: {str(e)}"
            raise Exception(error_msg)
        finally:
            if mongo_client:
                mongo_client.close()
    return wrapper

@mongodb_operation
def push_to_mongo(collection, data):
    data["timestamp"] = datetime.utcnow().strftime('%d-%m-%Y_%H-%M-%S')
    result = collection.insert_one(data)
    return (result.inserted_id)

@mongodb_operation
def update_lesson_plan_in_mongo(collection, document_id: str, lesson_plan: str):
    result = collection.update_one(
        {"_id": ObjectId(document_id)},
        {"$set": {"lesson_plan": lesson_plan}}
    )
    if result.modified_count > 0:
        print(f"Lesson plan updated for document ID: {document_id}")
    else:
        print(f"No document found or no changes made for ID: {document_id}")

# New function to get data from MongoDB
@mongodb_operation
def get_lesson_data(collection, mongo_id: str):
    try:
        # Try as ObjectId first
        try:
            obj_id = ObjectId(mongo_id)
            mongo_data = collection.find_one({"_id": obj_id})
        except ValueError:
            # If ObjectId fails, try as string
            mongo_data = collection.find_one({"_id": mongo_id})
        
        # logger.info(f"Query result: {mongo_data}")
        if not mongo_data:
            raise Exception(f"Document not found. Searched for ID: {mongo_id}")
        return mongo_data
    except Exception as e:
        raise Exception(f"Failed to retrieve data: {str(e)}")
    
def get_grade_name(grade_id: str) -> str:
    """
    Fetch the grade name from the grades collection using the grade_id.
    """
    mongo_client = None
    try:
        mongo_client = get_mongodb_connection()
        db = mongo_client.get_database(db_name)  
        grades_collection = db.get_collection("grades")  
        
        # Check if grade_id is a valid ObjectId
        if ObjectId.is_valid(grade_id):
            grade_doc = grades_collection.find_one({"_id": ObjectId(grade_id)})
        else:
            grade_doc = grades_collection.find_one({"_id": grade_id})  
        
        if grade_doc:
            return grade_doc.get("name", "Unknown Grade")
        return "Unknown Grade"
    except Exception as e:
        logger.error(f"Failed to fetch grade name for ID {grade_id}: {str(e)}")
        return "Unknown Grade"
    finally:
        if mongo_client:
            mongo_client.close()


def get_subject_name(subject_id: str) -> str:
    """
    Fetch the subject name from the subjects collection using the subject_id.
    """
    mongo_client = None
    try:
        mongo_client = get_mongodb_connection()
        db = mongo_client.get_database(db_name)
        subjects_collection = db.get_collection("subjects")  # Assuming collection is named 'subjects'
        
        # Check if subject_id is a valid ObjectId
        if ObjectId.is_valid(subject_id):
            subject_doc = subjects_collection.find_one({"_id": ObjectId(subject_id)})
        else:
            subject_doc = subjects_collection.find_one({"_id": subject_id})
        
        if subject_doc:
            return subject_doc.get("name", "Unknown Subject")
        return "Unknown Subject"
    except Exception as e:
        logger.error(f"Failed to fetch subject name for ID {subject_id}: {str(e)}")
        return "Unknown Subject"
    finally:
        if mongo_client:
            mongo_client.close()

            