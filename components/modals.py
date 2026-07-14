import streamlit as st


def confirm_modal(title: str, message: str, key: str) -> bool:
    st.warning(message)
    col1, col2 = st.columns(2)
    with col1:
        if st.button(f"Confirmar {title}", key=f"{key}_yes", type="primary"):
            return True
    with col2:
        st.button("Cancelar", key=f"{key}_no")
    return False
