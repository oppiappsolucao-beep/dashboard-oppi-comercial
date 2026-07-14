import pandas as pd
import plotly.express as px
import streamlit as st


def funnel_chart(steps: list[dict]):
    if not steps:
        st.info("Sem dados para exibir o funil.")
        return
    df = pd.DataFrame(steps)
    fig = px.funnel(df, x="count", y="name", color="name")
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        height=360,
    )
    st.plotly_chart(fig, use_container_width=True)


def revenue_chart(data: list[dict]):
    if not data:
        st.info("Sem dados financeiros para o período.")
        return
    df = pd.DataFrame(data)
    fig = px.bar(df, x="month", y=["meta", "realizado"], barmode="group")
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=360,
    )
    st.plotly_chart(fig, use_container_width=True)
