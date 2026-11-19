import streamlit as st
from agent import answer_question
from backend.app.core.config import ConfigurationError
from backend.app.services.gemini_service import GeminiServiceError, GeminiTimeoutError

st.title("🛒 E-commerce Assistant")
st.write("Ask a general product or shopping question.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

question = st.chat_input("Ask a shopping question...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                answer = answer_question(question)
            except ConfigurationError:
                st.error("The assistant is not configured. Set GEMINI_API_KEY and try again.")
            except GeminiTimeoutError:
                st.error("The Gemini request timed out. Please try again.")
            except GeminiServiceError:
                st.error("The assistant is temporarily unavailable. Please try again.")
            else:
                st.write(answer)
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer}
                )
