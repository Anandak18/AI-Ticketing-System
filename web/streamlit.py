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
st.set_page_config(page_title="Ticket Chatbot", page_icon="💬", layout="centered")
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

    url = "http://localhost:5000/api/chat"  # Replace with your API URL if different
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
# Display Chat with Bubbles and Markdown
# ------------------------------
chat_container = st.container()
with chat_container:
    for chat in st.session_state.chat_history:
        # User message
        st.markdown(f"""
        <div style='text-align: right; margin: 5px 0;'>
            <div style='display: inline-block; background-color: #DCF8C6; padding: 10px; border-radius: 10px; max-width: 70%; word-wrap: break-word;'>
                **You:** {chat['user']}  
                <span style='font-size: 10px; color: gray;'>{chat['timestamp']}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Bot message
        bot_response = chat["bot"]
        if isinstance(bot_response, dict):
            # Text message
            st.markdown(f"""
            <div style='text-align: left; margin: 5px 0;'>
                <div style='display: inline-block; background-color: #F1F0F0; padding: 10px; border-radius: 10px; max-width: 70%; word-wrap: break-word;'>
                    **Bot:** {bot_response.get("message", "")}  
                    <span style='font-size: 10px; color: gray;'>{chat['timestamp']}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Graph image
            graph_image = bot_response.get("graph_image")
            if graph_image:
                try:
                    image_bytes = base64.b64decode(graph_image)
                    image = Image.open(BytesIO(image_bytes))
                    st.image(image, caption="Generated Graph", use_container_width=True)
                except Exception as e:
                    st.error(f"Failed to display graph: {e}")
        else:
            # If bot_response is string
            st.markdown(f"""
            <div style='text-align: left; margin: 5px 0;'>
                <div style='display: inline-block; background-color: #F1F0F0; padding: 10px; border-radius: 10px; max-width: 70%; word-wrap: break-word;'>
                    **Bot:** {str(bot_response)}  
                    <span style='font-size: 10px; color: gray;'>{chat['timestamp']}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

# ------------------------------
# Auto Scroll to Latest Message
# ------------------------------
st.markdown("<div id='bottom'></div>", unsafe_allow_html=True)
st.markdown("<script>document.getElementById('bottom').scrollIntoView(true);</script>", unsafe_allow_html=True)
