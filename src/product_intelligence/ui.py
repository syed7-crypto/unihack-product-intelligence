"""Streamlit user interface for the product intelligence MVP."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from .pipeline import (
    ProductIntelligencePipelineError,
    ProductIntelligenceResult,
    run_pipeline,
)


def render_app() -> None:
    """Render the upload, analysis, and structured result experience."""
    st.set_page_config(
        page_title="UniHack Product Intelligence",
        page_icon="📦",
        layout="wide",
    )

    st.title("UniHack Product Intelligence")
    st.caption("Turn product documents into structured, source-backed intelligence.")

    uploaded_files = st.file_uploader(
        "Upload product source files",
        type=["pdf", "txt", "json"],
        accept_multiple_files=True,
        help="Supported formats: PDF, TXT, and JSON.",
    )

    if uploaded_files:
        st.subheader("Selected sources")
        st.dataframe(
            [
                {
                    "File": uploaded_file.name,
                    "Type": _file_type(uploaded_file.name),
                    "Size": _format_size(uploaded_file.size),
                }
                for uploaded_file in uploaded_files
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("Upload one or more PDF, TXT, or JSON files to begin.")

    if st.button(
        "Analyze product",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    ):
        result = _analyze_uploaded_files(uploaded_files)
        if result is not None:
            st.session_state["product_intelligence_result"] = result

    result = st.session_state.get("product_intelligence_result")
    if isinstance(result, ProductIntelligenceResult):
        _render_result(result)


def _analyze_uploaded_files(uploaded_files: list[Any]) -> ProductIntelligenceResult | None:
    """Write uploads to a temporary directory and run the existing pipeline."""
    try:
        with st.status("Analyzing product sources…", expanded=True) as status:
            status.write("Extracting PDF, TXT, and JSON sources")
            with tempfile.TemporaryDirectory(prefix="unihack_sources_") as directory:
                source_paths: list[Path] = []
                for index, uploaded_file in enumerate(uploaded_files):
                    original_name = Path(uploaded_file.name).name
                    path = Path(directory) / f"{index}_{original_name}"
                    path.write_bytes(uploaded_file.getvalue())
                    source_paths.append(path)

                status.write("Running product identification and attribute extraction")
                result = run_pipeline(source_paths)
                status.write("Validating sources and calculating confidence")
            status.update(label="Analysis complete", state="complete", expanded=False)
        return result
    except ProductIntelligencePipelineError as error:
        st.error(f"Analysis could not be completed: {error}")
    except Exception:
        # Keep implementation details and credentials out of the normal UI.
        st.error("Analysis could not be completed due to an unexpected application error.")
    return None


def _render_result(result: ProductIntelligenceResult) -> None:
    identification = result.product_identification
    st.divider()
    st.header("Product intelligence")
    product_column, category_column = st.columns(2)
    product_column.metric("Product", identification.product_type)
    category_column.metric("Category", identification.product_category)

    st.subheader("Attributes")
    st.dataframe(
        build_attribute_rows(result),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Status": st.column_config.TextColumn("Status", width="medium"),
            "Confidence": st.column_config.TextColumn("Confidence", width="small"),
        },
    )

    st.subheader("Evidence by attribute")
    _render_evidence(result)

    st.subheader("Validation and conflicts")
    conflict_rows = build_conflict_rows(result)
    if conflict_rows:
        for conflict in conflict_rows:
            st.warning(
                f"{conflict['Attribute']}: {conflict['Values']} — "
                "conflict requires review"
            )
    else:
        st.success("No cross-source conflicts detected.")

    st.subheader("Confidence explanations")
    st.dataframe(
        [
            {
                "Attribute": assessment.name,
                "Score": assessment.score,
                "Level": assessment.level,
                "Reasons": " · ".join(assessment.reasons),
            }
            for assessment in result.confidence.attributes
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Export")
    st.download_button(
        "Download structured JSON",
        data=result.model_dump_json(indent=2),
        file_name="product_intelligence_result.json",
        mime="application/json",
        use_container_width=True,
    )


def build_attribute_rows(result: ProductIntelligenceResult) -> list[dict[str, str]]:
    """Build display rows without changing the backend result."""
    labels = {attribute.name: attribute.label for attribute in result.dynamic_attribute_schema}
    confidence = {assessment.name: assessment for assessment in result.confidence.attributes}
    rows: list[dict[str, str]] = []
    for attribute in result.validation.attributes:
        assessment = confidence[attribute.name]
        if not attribute.values:
            value = "Not found"
        else:
            value = " / ".join(item.value for item in attribute.values)
        status = "⚠ Conflict" if attribute.status == "conflict" else _status_label(attribute.status)
        rows.append(
            {
                "Attribute": labels.get(attribute.name, attribute.name),
                "Value": value,
                "Status": status,
                "Confidence": f"{assessment.level.title()} ({assessment.score:.2f})",
            }
        )
    return rows


def build_conflict_rows(result: ProductIntelligenceResult) -> list[dict[str, str]]:
    """Build compact conflict rows for the validation summary."""
    labels = {attribute.name: attribute.label for attribute in result.dynamic_attribute_schema}
    return [
        {
            "Attribute": labels.get(attribute.name, attribute.name),
            "Values": " / ".join(item.value for item in attribute.values),
        }
        for attribute in result.validation.attributes
        if attribute.status == "conflict"
    ]


def _render_evidence(result: ProductIntelligenceResult) -> None:
    labels = {attribute.name: attribute.label for attribute in result.dynamic_attribute_schema}
    for attribute in result.validation.attributes:
        with st.expander(f"{labels.get(attribute.name, attribute.name)} — {attribute.status}"):
            if not attribute.values:
                st.write("No supporting evidence found.")
                continue
            for item in attribute.values:
                evidence = item.evidence
                location = evidence.location or "Location unavailable"
                st.markdown(f"**{evidence.source_name}** · {location}")
                st.write(f"Value: {item.value}")
                st.caption(f'“{evidence.quote}”')


def _status_label(status: str) -> str:
    return {
        "consistent": "Consistent",
        "single_source": "Single source",
        "not_found": "Not found",
    }.get(status, status.replace("_", " ").title())


def _file_type(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".").upper() or "Unknown"


def _format_size(size: int | None) -> str:
    if size is None:
        return "Unknown"
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KB"
