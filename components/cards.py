import streamlit as st


def metric_card(label: str, value, note: str = "", tone: str = "purple", icon: str = "✦"):
    st.markdown(
        f"""
        <div class="metric-card tone-{tone}">
          <div class="metric-icon">{icon}</div>
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_cards(items: list[dict]):
    cols = st.columns(len(items) if items else 1)
    for col, item in zip(cols, items):
        with col:
            metric_card(
                label=item.get("label", ""),
                value=item.get("value", 0),
                note=item.get("note", ""),
                tone=item.get("tone", "purple"),
                icon=item.get("icon", "✦"),
            )
