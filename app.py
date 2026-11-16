import streamlit as st
from agent import crew

st.title("🛒 E-commerce Order Assistant")
st.write("Ask about any order. Example: *Where is my order ORD1003?* or *Show all orders of Priya Sharma*")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

question = st.chat_input("Ask about your order...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Checking the order database..."):
            result = crew.kickoff(inputs={"question": question})
            answer = result.raw
        st.write(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
