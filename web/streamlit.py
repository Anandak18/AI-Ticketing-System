# streamlit_ticket_chatbot.py
import streamlit as st
import requests
from datetime import datetime
import base64
from io import BytesIO
from PIL import Image

# ------------------------------
# Page Config
# ------------------------------
st.set_page_config(page_title="Ticket Chatbot", page_icon="💬", layout="centered", initial_sidebar_state="collapsed")

# ------------------------------
# Custom CSS for look and feel
# ------------------------------
st.markdown("""
<style>
/* Dark theme background */
body, .stApp {
     background-color: #F9F9F9;  /* Soft light background */
    color: #1E1E1E;  
}

/* Chat container */
.chat-container {
    max-height: 600px;
    overflow-y: auto;
    padding: 10px;
}

/* Chat bubbles */
.user-bubble {
    background-color: #0B93F6;
    color: white;
    padding: 10px 15px;
    border-radius: 15px;
    display: inline-block;
    max-width: 70%;
    margin-bottom: 5px;
    box-shadow: 2px 2px 5px rgba(0,0,0,0.3);
}

.bot-bubble {
    background-color: #2C2C2C;
    color: #FFFFFF;
    padding: 10px 15px;
    border-radius: 15px;
    display: inline-block;
    max-width: 70%;
    margin-bottom: 5px;
    box-shadow: 2px 2px 5px rgba(0,0,0,0.3);
}

/* Timestamp */
.timestamp {
    font-size: 10px;
    color: gray;
    margin-left: 5px;
}

/* Avatars */
.avatar {
    width: 25px;
    height: 25px;
    border-radius: 50%;
    vertical-align: middle;
    margin-right: 5px;
}
</style>
""", unsafe_allow_html=True)



def get_base64_image(image_path):
    with open(image_path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode()

# Encode images
human_img = get_base64_image("images/human.jpeg")
bot_img = get_base64_image("images/Robot.jpg")


st.title("💬 Ticket Management Chatbot")

# ------------------------------
# Initialize Session State
# ------------------------------
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ------------------------------
# Function to send message to FastAPI
# ------------------------------
def send_message_to_api(message: str):
    if not message.strip():
        return {"status": "error", "message": "Please enter a message."}

    url = "http://localhost:5000/api/chat"
    payload = {"message": message}

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, str):
                return {"status": "success", "message": data}
            return data
        else:
            return {"status": "error", "message": f"Error {response.status_code}: {response.text}"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to connect to API: {e}"}

# ------------------------------
# Chat Input Form
# ------------------------------
with st.form(key="chat_form", clear_on_submit=True):
    user_input = st.text_input("Type your message here...")
    submitted = st.form_submit_button("Send")
    if submitted and user_input:
        bot_response = send_message_to_api(user_input)
        st.session_state.chat_history.append({
            "user": user_input,
            "bot": bot_response,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })

# ------------------------------
# Display Chat
# ------------------------------
chat_container = st.container()
with chat_container:
    st.markdown('<div class="chat-container">', unsafe_allow_html=True)
    for chat in st.session_state.chat_history:
        timestamp = chat['timestamp']
        # User bubble
        st.markdown(f"""
        <div style='text-align: right; margin: 5px 0;'>
            <div class='user-bubble'>
                <img class='avatar' src="data:image/png;base64,{human_img}"> 
                <strong>You:</strong> {chat['user']}  
                <span class='timestamp'>{timestamp}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Bot bubble
        bot_response = chat["bot"]
        if isinstance(bot_response, dict):
            st.markdown(f"""
            <div style='text-align: left; margin: 5px 0;'>
                <div class='bot-bubble'>
                    <img class='avatar' src="data:image/png;base64,{bot_img}"> 
                    <strong>Bot:</strong> {bot_response.get("message", "")}  
                    <span class='timestamp'>{timestamp}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            graph_image = bot_response.get("graph_image")
            if graph_image:
                try:
                    image_bytes = base64.b64decode(graph_image)
                    image = Image.open(BytesIO(image_bytes))
                    st.image(image, caption="Generated Graph", use_container_width=True)
                except Exception as e:
                    st.error(f"Failed to display graph: {e}")
        else:
            st.markdown(f"""
            <div style='text-align: left; margin: 5px 0;'>
                <div class='bot-bubble'>
                    <img class='avatar' src='https://i.imgur.com/8Km9tLL.png'> 
                    <strong>Bot:</strong> {str(bot_response)}  
                    <span class='timestamp'>{timestamp}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ------------------------------
# Auto Scroll to Latest Message
# ------------------------------
st.markdown("<div id='bottom'></div>", unsafe_allow_html=True)
st.markdown("<script>document.getElementById('bottom').scrollIntoView(true);</script>", unsafe_allow_html=True)
