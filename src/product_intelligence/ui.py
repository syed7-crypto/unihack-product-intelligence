"""Initial Streamlit user interface."""

import streamlit as st


def render_app() -> None:
    """Render the initial application shell."""
    st.set_page_config(
        page_title="UniHack 2026 AI Product Intelligence",
        page_icon="📦",
        layout="centered",
    )

    st.title("UniHack 2026 AI Product Intelligence")
    st.write(
        "AI-powered product intelligence for turning industrial product "
        "information into structured, commerce-ready data."
    )
    st.info("The product intelligence pipeline is under development.")
