import pandas as pd
import streamlit as st


def data_table(rows: list[dict], columns: list[str] | None = None):
    if not rows:
        st.info("Nenhum registro encontrado.")
        return
    df = pd.DataFrame(rows)
    if columns:
        existing = [c for c in columns if c in df.columns]
        df = df[existing]
    st.dataframe(df, use_container_width=True, hide_index=True)
