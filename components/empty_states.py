import streamlit as st


def empty_state(title: str, message: str, action_label: str | None = None, action_key: str = "empty_action"):
    st.markdown(
        f"""
        <div class="empty-state">
          <div class="empty-title">{title}</div>
          <div class="empty-message">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if action_label:
        return st.button(action_label, key=action_key)
    return False
