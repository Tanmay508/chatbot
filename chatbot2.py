import os
import json
import re
import logging
from pymongo import MongoClient
from dotenv import load_dotenv
import serpapi
from datetime import datetime, timezone
import requests
import streamlit as st
import bcrypt
from googletrans import Translator, LANGUAGES
from fuzzywuzzy import fuzz
import speech_recognition as sr
import pyaudio
import wave
import time

# Set wide layout for Streamlit
st.set_page_config(layout="wide")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("pymongo").setLevel(logging.WARNING)

# Load environment variables
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
SERPAPI_KEY = os.getenv("82f983b2d2949c0c0505c6f2061759ff")

# MongoDB setup
try:
    client = MongoClient(MONGO_URI)
    db = client["vikas_content"]
    conversations = db["conversations"]
    commodity_prices = db["commodity_prices"]
    users = db["users"]
    logging.info("Connected to MongoDB: vikas_content database")
except Exception as e:
    logging.error(f"MongoDB connection error: {e}")
    st.error("Failed to connect to MongoDB. Please check your connection.")
    st.stop()

# Load commodities from txt file
def load_commodities(file_path="commodity_list.txt"):
    try:
        with open(file_path, "r") as file:
            commodities = [line.strip().lower() for line in file if line.strip() and "ox" not in line.lower()]
        commodities = list(dict.fromkeys(commodities))
        logging.info(f"Loaded {len(commodities)} commodities from {file_path}")
        return commodities
    except Exception as e:
        logging.error(f"Error loading commodities from {file_path}: {e}. Ensure commodity_list.txt exists.")
        return [
            "banana - green", "beans", "beetroot", "betal leaves", "bhindi", "ladies finger",
            "bitter gourd", "bottle gourd", "brinjal", "cabbage", "capsicum", "carrot",
            "cauliflower", "cucumber", "kheera", "fish", "garlic", "ginger(dry)",
            "ginger(green)", "green chilli", "guar", "little gourd", "kundru", "maize",
            "onion", "paddy", "dhan", "papaya (raw)", "pointed gourd", "parval", "potato",
            "aloo", "pumpkin", "rice", "chawla", "ridgeguard", "tori", "tomato",
            "water melon", "yam", "ratalu", "jack fruit", "lemon", "nimbu", "corn",
            "broccoli", "colacasia", "cluster beans", "raddish", "green peas", "drumstick",
            "wheat"
        ]

commodities = load_commodities()

# Commodity translation mapping
COMMODITY_MAPPING = {
    "okra": "bhindi",
    "ladies finger": "bhindi",
    "lemon": "nimbu",
    "cucumber": "kheera",
    "paddy": "dhan",
    "rice": "chawla",
    "pointed gourd": "parval",
    "potato": "aloo",
    "ridgeguard": "tori",
    "yam": "ratalu",
    "brocoli": "broccoli",
    "ginger": "ginger(green)",
    "dry ginger": "ginger(dry)",
    "chilli": "green chilli",
    "papaya": "papaya (raw)",
    "sugar beet": "beetroot",
    "chukandar": "beetroot"
}

# Ollama API setup
OLLAMA_URL = "http://115.124.125.174:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# Prompt template
PROMPT_TEMPLATE = """
You are a helpful chatbot focused on agriculture, farming, and agricultural equipment. The user asked: "{user_input}"
- Understand the user's intent.
- Provide a concise, accurate response related to agriculture, farming, or agricultural equipment.
- For price queries, suggest checking local markets or government sources if specific data is unavailable.
- If the query is unclear or outside this scope, politely inform the user that you can only answer agricultural questions.
Response:
"""

# Initialize translator
translator = Translator()

# User management
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed)

def register_user(username, password):
    try:
        if users.find_one({"username": username}):
            return False, "Username already exists."
        hashed = hash_password(password)
        users.insert_one({"username": username, "password": hashed})
        logging.info(f"User {username} registered")
        return True, "Registration successful."
    except Exception as e:
        logging.error(f"Registration error: {e}")
        return False, "Registration failed."

def login_user(username, password):
    try:
        user = users.find_one({"username": username})
        if user and check_password(password, user["password"]):
            logging.info(f"User {username} logged in")
            return True, user["username"]
        return False, "Invalid username or password."
    except Exception as e:
        logging.error(f"Login error: {e}")
        return False, "Login failed."

# Search commodity prices
def search_commodity_prices(query):
    try:
        query_lower = query.lower().strip()
        logging.debug(f"Processing commodity query: {query_lower}")

        price_keywords = ["price", "cost", "rate", "value", "prie", "कीमत"]
        is_price_query = False
        for keyword in price_keywords:
            if any(fuzz.ratio(keyword, word) > 80 for word in query_lower.split()):
                is_price_query = True
                break
        if not is_price_query:
            logging.debug("No price-related keywords found")
            return None

        matched_commodity = None
        for commodity in commodities:
            if (commodity in query_lower or
                any(fuzz.ratio(commodity, word) > 85 for word in query_lower.split())):
                matched_commodity = commodity
                break
        if not matched_commodity:
            logging.debug("No matching commodity found")
            return None

        matched_commodity = COMMODITY_MAPPING.get(matched_commodity, matched_commodity)
        logging.debug(f"Matched commodity: {matched_commodity}")

        location_filters = {}
        if "balasore" in query_lower or "baleswar" in query_lower or "baleshwar" in query_lower:
            location_filters["district_name"] = {"$regex": "Balasore|Baleswar|Baleshwar", "$options": "i"}
        if "odisha" in query_lower or "orissa" in query_lower:
            location_filters["state_name"] = {"$regex": "Odisha|Orissa", "$options": "i"}
        if "hindol" in query_lower:
            location_filters["market"] = {"$regex": "Hindol", "$options": "i"}
        if "rayagada" in query_lower:
            location_filters["district_name"] = {"$regex": "Rayagada", "$options": "i"}

        logging.debug(f"Location filters: {location_filters}")

        query_dict = {"crop": {"$regex": matched_commodity, "$options": "i"}}
        if location_filters:
            query_dict.update(location_filters)

        logging.debug(f"Database query: {query_dict}")
        result = commodity_prices.find_one(
            query_dict,
            sort=[("arrival_date", -1)]
        )
        logging.debug(f"Database result: {result}")

        if result:
            response = (f"The price of {result['crop']} in {result['market']}, {result['district_name']}, "
                        f"{result['state_name']} is {result['modal_price']} {result['unit_of_price']} "
                        f"as of {result['arrival_date'].split('T')[0]}.")
            logging.debug(f"Found price data: {response}")
            return response

        if location_filters.get("state_name"):
            state_query = {
                "crop": {"$regex": matched_commodity, "$options": "i"},
                "state_name": location_filters["state_name"]
            }
            logging.debug(f"State query: {state_query}")
            state_result = commodity_prices.find_one(
                state_query,
                sort=[("arrival_date", -1)]
            )
            logging.debug(f"State result: {state_result}")
            if state_result:
                response = (f"The price of {state_result['crop']} in {state_result['market']}, "
                            f"{state_result['state_name']} is {state_result['modal_price']} "
                            f"{state_result['unit_of_price']} as of {state_result['arrival_date'].split('T')[0]}.")
                logging.debug(f"Found state-level price data: {response}")
                return response

        logging.debug("No price data found in commodity_prices")
        return None
    except Exception as e:
        logging.error(f"Commodity prices search error: {e}")
        return None

# Web search fallback
def web_search(query, is_price_query=False):
    try:
        search_query = query
        if is_price_query:
            search_query = f"current price of {query} today"

        params = {
            "q": search_query,
            "api_key": SERPAPI_KEY,
            "num": 5
        }
        search = serpapi.GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        logging.debug(f"Web search query: {search_query}, Results: {len(results)}")
        if results:
            for result in results:
                snippet = result.get("snippet", "")
                logging.debug(f"Evaluating snippet: {snippet}")
                if any(unit in snippet.lower() for unit in ["rs", "inr", "rupees", "per kg", "per quintal"]):
                    logging.debug(f"Selected web search result: {snippet}")
                    return snippet
            response = results[0]["snippet"]
            logging.debug(f"Web search fallback result: {response}")
            return response
        logging.debug("No web search results found")
        return None
    except Exception as e:
        logging.error(f"Web search error: {e}")
        return None

# Get LLaMA response via Ollama API
def get_llama_response(user_input):
    prompt = PROMPT_TEMPLATE.format(user_input=user_input)
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "max_tokens": 150,
                "temperature": 0.7,
                "top_p": 0.9
            }
        )
        response.raise_for_status()
        full_response = ""
        for line in response.text.splitlines():
            if line.strip():
                data = json.loads(line)
                full_response += data.get("response", "")
                if data.get("done"):
                    break
        result = full_response.split("Response:")[-1].strip()
        logging.debug(f"LLaMA response: {result}")
        return result
    except Exception as e:
        logging.error(f"Ollama error: {e}")
        return None

# Process user query with multilingual support
def process_query(user_input, input_lang='en'):
    logging.info(f"Processing user query: {user_input} (lang: {input_lang})")
    if not user_input.strip():
        logging.debug("Empty query, skipping")
        return translate_response("Please enter a valid query.", input_lang), input_lang

    agriculture_keywords = [
        "agriculture", "farming", "crop", "commodity", "price", "rate", "cost", "equipment",
        "tractor", "harvest", "irrigation", "lady finger", "bhindi", "rice", "wheat", "potato",
        "pests", "insects", "disease", "weeds", "crop protection", "fertilizer", "soil", "seeds",
        "banana", "beans", "beetroot", "cucumber", "fish", "garlic", "ginger", "chilli", "maize",
        "onion", "paddy", "papaya", "lemon", "corn", "broccoli", "peas", "bugs", "vermin",
        "crop damage"
    ]
    hindi_agriculture_keywords = [
        "कृषि", "खेती", "फसल", "कीट", "रोग", "खरपतवार", "उर्वरक", "मिट्टी", "बीज",
        "प्याज", "आलू", "चावल", "गेहूं", "नींबू", "चुकंदर", "कीमत"
    ]

    is_agriculture_related = False
    if input_lang == 'en':
        if any(keyword in user_input.lower() for keyword in agriculture_keywords):
            is_agriculture_related = True
    elif input_lang == 'hi':
        if any(keyword in user_input for keyword in hindi_agriculture_keywords):
            is_agriculture_related = True

    query_to_process = user_input
    translated_query = Nonerobotics = None
    if input_lang != 'en':
        try:
            translated = translator.translate(user_input, src=input_lang, dest='en')
            translated_query = translated.text
            query_to_process = translated_query
            logging.debug(f"Translated query to English: {query_to_process}")
            if any(keyword in query_to_process.lower() for keyword in agriculture_keywords):
                is_agriculture_related = True
        except Exception as e:
            logging.error(f"Translation error: {e}")
            query_to_process = user_input
            logging.debug("Using original query due to translation failure")

    if not is_agriculture_related:
        logging.debug(f"Query not agriculture-related. Original: {user_input}, Translated: {translated_query}")
        return translate_response("Sorry, I can only answer questions about agriculture and farming related questions.", input_lang), input_lang

    price_keywords = ["price", "cost", "rate", "value", "prie", "कीमत"]
    is_price_query = any(fuzz.ratio(keyword, word) > 80 for keyword in price_keywords for word in query_to_process.lower().split())

    commodity_answer = search_commodity_prices(query_to_process)
    if commodity_answer:
        return translate_response(commodity_answer, input_lang), input_lang

    if is_price_query:
        web_result = web_search(query_to_process, is_price_query=True)
        if web_result:
            return translate_response(f"From the web: {web_result}", input_lang), input_lang
        return translate_response(f"Sorry, I couldn't find recent price data for {query_to_process}. Try checking local markets or government sources.", input_lang), input_lang

    llama_response = get_llama_response(query_to_process)
    if llama_response and "sorry" not in llama_response.lower():
        return translate_response(llama_response, input_lang), input_lang

    web_result = web_search(query_to_process)
    if web_result:
        return translate_response(f"From the web: {web_result}", input_lang), input_lang

    logging.debug("No answer found")
    return translate_response("Sorry, I couldn't find an answer. Please try rephrasing your query or check local agricultural sources.", input_lang), input_lang

# Translate response to user's language
def translate_response(text, target_lang):
    if target_lang == 'en':
        return text
    try:
        translated = translator.translate(text, src='en', dest=target_lang)
        logging.debug(f"Translated response to {target_lang}: {translated.text}")
        return translated.text
    except Exception as e:
        logging.error(f"Response translation error: {e}")
        return text

# Save conversation
def save_conversation(user_id, user_message, bot_response, input_lang):
    try:
        conversation = {
            "user_id": user_id,
            "user_message": user_message,
            "bot_response": bot_response,
            "input_language": input_lang,
            "timestamp": datetime.now(timezone.utc)
        }
        conversations.insert_one(conversation)
        logging.debug("Conversation saved")
    except Exception as e:
        logging.error(f"Error saving conversation: {e}")

# Record audio function
def record_audio(duration=60, lang="en-IN"):
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        st.warning(f"Listening... Speak now!")
        audio = recognizer.listen(source, timeout=duration)
        st.warning("Listening stopped. Processing...")
    try:
        transcript = recognizer.recognize_google(audio, language=lang)
        return transcript
    except sr.UnknownValueError:
        st.error("Could not understand audio")
        return ""
    except sr.RequestError as e:
        st.error(f"Could not request results; {e}")
        return ""

# Streamlit UI
def main():
    # Custom CSS to revert to older layout
    st.markdown("""
        <style>
            .sidebar .sidebar-content {
                padding: 10px;
                background-color: #f0f0f0;
                border-right: 1px solid #ccc;
            }
            .chat-container {
                padding: 20px;
                background-color: white;
            }
            .message {
                margin: 10px 0;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            .user-message {
                background-color: #e6f3ff;
                text-align: right;
            }
            .bot-message {
                background-color: #f9f9f9;
            }
            .input-container {
                padding: 10px;
                background-color: #fff;
                border-top: 1px solid #ccc;
            }
            .input-container input {
                width: 70%;
                padding: 5px;
                margin-right: 10px;
            }
            .input-container button {
                padding: 5px 15px;
                background-color: #ff4d4d;
                color: white;
                border: none;
                cursor: pointer;
            }
            .input-container button:hover {
                background-color: #cc0000;
            }
            .voice-btn {
                padding: 5px 10px;
                background-color: #4d79ff;
                color: white;
                border: none;
                cursor: pointer;
                margin-right: 10px;
            }
            .voice-btn:hover {
                background-color: #0033cc;
            }
        </style>
    """, unsafe_allow_html=True)

    # Initialize session state
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.messages = []
        st.session_state.dark_mode = False
    if "input_key" not in st.session_state:
        st.session_state.input_key = 0
    if "user_input" not in st.session_state:
        st.session_state.user_input = ""

    # Login/Registration UI
    if not st.session_state.logged_in:
        st.title("AgriBot Login")
        tab1, tab2 = st.tabs(["Login", "Register"])

        with tab1:
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submit = st.form_submit_button("Login")
                if submit:
                    success, message = login_user(username, password)
                    if success:
                        st.session_state.logged_in = True
                        st.session_state.username = username
                        st.success("Logged in successfully!")
                        st.rerun()
                    else:
                        st.error(message)

        with tab2:
            with st.form("register_form"):
                new_username = st.text_input("New Username")
                new_password = st.text_input("New Password", type="password")
                register = st.form_submit_button("Register")
                if register:
                    success, message = register_user(new_username, new_password)
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
        return

    # Header
    st.sidebar.title("Conversation History")
    user_conversations = conversations.find({"user_id": st.session_state.username}).sort("timestamp", -1)
    if not list(user_conversations.clone()):
        st.sidebar.write("No conversation history yet.")
    else:
        user_conversations.rewind()
        for conv in user_conversations:
            st.sidebar.write(f"You: {conv['user_message']}")
            st.sidebar.write(f"Bot: {conv['bot_response']}")
            st.sidebar.write(f"Time: {conv['timestamp'].strftime('%Y-%m-%d %H:%M')}")

    # Chat area
    st.title("Agri-Chat")
    for msg in st.session_state.messages:
        message_class = "user-message" if msg["role"] == "user" else "bot-message"
        st.markdown(f'<div class="message {message_class}">{msg["content"]}</div>', unsafe_allow_html=True)

    # Input area
    supported_languages = {
        "English": "en",
        "Hindi": "hi",
        "Marathi": "mr",
        "Tamil": "ta",
        "Telugu": "te",
        "Bengali": "bn",
        "Gujarati": "gu",
        "Kannada": "kn",
        "Malayalam": "ml",
        "Punjabi": "pa"
    }
    selected_lang = st.selectbox("Select Language", list(supported_languages.keys()), index=0, key="lang_select")
    input_lang_code = supported_languages[selected_lang]

    # Voice recording button outside the form
    if st.button("🎙️ Speak", key=f"voice_{st.session_state.input_key}", help="Click to speak"):
        with st.spinner("Processing audio..."):
            transcript = record_audio(duration=60, lang=f"{input_lang_code}-IN")
            if transcript:
                st.session_state.user_input = transcript

    with st.form(key="chat_form", clear_on_submit=True):
        # Text input for user query
        user_input = st.text_input("Ask about agriculture...", key=f"input_{st.session_state.input_key}", value=st.session_state.user_input, placeholder="Ask about agriculture...")
        submit = st.form_submit_button("Send")
        if submit and user_input.strip():
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.messages.append({"role": "user", "content": user_input, "timestamp": timestamp})

            response, returned_lang = process_query(user_input, input_lang_code)

            st.session_state.messages.append({"role": "assistant", "content": response, "timestamp": timestamp})
            save_conversation(st.session_state.username, user_input, response, selected_lang)

            st.session_state.input_key += 1
            st.session_state.user_input = ""
            st.rerun()

if __name__ == "__main__":
    main()