from datetime import date

import streamlit as st


def period_filter(key_prefix: str = "period") -> tuple[date | None, date | None]:
    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input("Período início", value=None, key=f"{key_prefix}_start")
    with col2:
        end = st.date_input("Período fim", value=None, key=f"{key_prefix}_end")
    return start, end


def seller_filter(users: list, key: str = "seller_filter", include_all: bool = True) -> int | None:
    options = {}
    if include_all:
        options["Todos"] = None
    for user in users:
        options[user.name] = user.id
    label = st.selectbox("Vendedor", list(options.keys()), key=key)
    return options[label]
