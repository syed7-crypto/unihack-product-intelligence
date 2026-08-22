"""Streamlit demo UI for the governed UniHack catalogue pipeline."""

from __future__ import annotations

import csv
import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import streamlit as st

from .catalog_input import CatalogInputRow, load_catalog_rows
from .catalogue_batch import BatchResult, run_catalogue_batch
from .delivery_schema import load_delivery_schema
from .source_discovery import SerperSearchProvider


PAGE_NAMES = ("Run", "Results", "Review", "Delivery")


def main() -> None:
    """Package-safe entry point used by the root Streamlit launcher."""
    render_app()


def render_app() -> None:
    """Render the catalogue workflow using the existing backend APIs."""
    st.set_page_config(
        page_title="UniHack Product Intelligence",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()

    st.sidebar.markdown("## ◈ UniHack Intelligence")
    st.sidebar.caption("Evidence-backed catalogue enrichment")
    page = st.sidebar.radio("Workspace", PAGE_NAMES, index=0)

    if page == "Run":
        _render_run_page()
    elif page == "Results":
        _render_results_page()
    elif page == "Review":
        _render_review_page()
    else:
        _render_delivery_page()


def _render_run_page() -> None:
    st.markdown("# Enrichment control room")
    st.markdown(
        "Run the governed catalogue pipeline and monitor each trust boundary "
        "from discovery to delivery."
    )

    batch = _get_batch()
    _render_pipeline_stages(batch, running=False)
    st.divider()

    left, right = st.columns((1.45, 1), gap="large")
    with left:
        st.subheader("Inputs")
        catalogue_file = st.file_uploader(
            "Catalogue input CSV",
            type=["csv"],
            help="Expected raw fields include Mfg_Part_Num, Part_Desc, brand fields, and Part_Manuf.",
        )
        rows: list[CatalogInputRow] = []
        if catalogue_file is not None:
            rows = _preview_catalogue(catalogue_file)
            st.caption(f"{len(rows):,} catalogue rows loaded")

    with right:
        st.subheader("Run configuration")
        schema_file, schema_path = _schema_input()
        discovery_enabled = st.toggle(
            "Enable governed source discovery",
            value=True,
            help="Uses the configured Serper provider and existing policy/runtime trust boundaries.",
        )
        st.caption("Search results remain untrusted until approved retrieval and exact-MPN verification.")

    if catalogue_file is None:
        _render_empty_state(
            "Start with a catalogue CSV",
            "The run uses the real catalogue batch API. No enrichment results are fabricated in the UI.",
        )
        return
    if schema_file is None and schema_path is None:
        st.warning(
            "The official 252-column delivery header is not available. Upload the expected-output CSV "
            "or configure UNIHACK_DELIVERY_SCHEMA_PATH before running."
        )
        return

    st.divider()
    action_col, state_col = st.columns((1, 1.8), gap="large")
    with action_col:
        run_clicked = st.button(
            "▶  Run enrichment",
            type="primary",
            use_container_width=True,
            disabled=not rows,
        )
    with state_col:
        st.caption(
            "Ready to run" if rows else "Upload a valid catalogue to enable the run."
        )

    if run_clicked:
        started = time.perf_counter()
        batch = _run_catalogue(catalogue_file, schema_file, schema_path, discovery_enabled)
        st.session_state["catalogue_run_duration"] = time.perf_counter() - started
        if batch is not None:
            st.session_state["catalogue_batch_result"] = batch
            st.session_state["catalogue_filename"] = catalogue_file.name
            st.rerun()

    batch = _get_batch()
    if batch is not None:
        st.divider()
        st.subheader("Run summary")
        _render_run_metrics(batch)
        _render_pipeline_stages(batch, running=False)


def _render_pipeline_stages(batch: BatchResult | None, *, running: bool) -> None:
    """Show the fixed pipeline stages without duplicating backend behavior."""
    stages = (
        ("01", "Discovery", "Candidate sources"),
        ("02", "Verification", "HTTPS + exact MPN"),
        ("03", "Identification", "Product schema"),
        ("04", "Enrichment", "Evidence-backed values"),
        ("05", "Validation", "Confidence + conflicts"),
        ("06", "Delivery", "252-column output"),
    )
    if running:
        state = "running"
    elif batch is not None:
        state = "complete"
    else:
        state = "pending"
    cols = st.columns(len(stages), gap="small")
    for column, (number, name, detail) in zip(cols, stages):
        with column:
            marker = "●" if state == "complete" else ("◌" if state == "running" else "○")
            st.markdown(
                f'<div class="pipeline-stage {state}">'
                f'<div class="stage-marker">{marker} {number}</div>'
                f'<div class="stage-name">{name}</div>'
                f'<div class="stage-detail">{detail}</div></div>',
                unsafe_allow_html=True,
            )


def _render_run_metrics(batch: BatchResult) -> None:
    verified_sources = sum(
        sum(d.success for d in result.source_diagnostics)
        for result in batch.row_results
    )
    accepted_attributes = sum(
        sum(d.status == "mapped" for d in result.mapping_diagnostics)
        for result in batch.row_results
    )
    duration = st.session_state.get("catalogue_run_duration")
    duration_label = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "—"
    metrics = (
        ("Products", batch.processed_rows),
        ("Verified sources", verified_sources),
        ("Accepted attributes", accepted_attributes),
        ("Needs review", batch.needs_review_rows),
        ("Blocked", batch.blocked_rows),
        ("Duration", duration_label),
    )
    cols = st.columns(len(metrics), gap="small")
    for column, (label, value) in zip(cols, metrics):
        column.metric(label, value)


def _schema_input() -> tuple[Any | None, Path | None]:
    """Use an uploaded schema or a configured local schema path."""
    uploaded = st.file_uploader(
        "Delivery schema CSV",
        type=["csv"],
        help="Only the header is used to enforce the official 252-column output shape.",
        key="delivery_schema_upload",
    )
    configured = os.getenv("UNIHACK_DELIVERY_SCHEMA_PATH", "").strip()
    if configured and Path(configured).exists():
        st.caption(f"Using configured schema: {Path(configured).name}")
        return uploaded, Path(configured)
    return uploaded, None


def _preview_catalogue(uploaded: Any) -> list[CatalogInputRow]:
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(uploaded.getvalue())
            temp_path = Path(handle.name)
        try:
            rows = load_catalog_rows(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
        st.dataframe(
            [row.model_dump() for row in rows[:8]],
            hide_index=True,
            use_container_width=True,
        )
        return rows
    except Exception as error:
        st.error(f"Catalogue CSV could not be parsed: {error}")
        return []


def _run_catalogue(
    catalogue_file: Any,
    schema_file: Any | None,
    schema_path: Path | None,
    discovery_enabled: bool,
) -> BatchResult | None:
    started = time.perf_counter()
    progress = st.container(border=True)
    with progress:
        st.markdown("### Live execution")
        activity = st.empty()
        current_stage = st.empty()
        completed_stages = st.empty()
        execution_detail = st.empty()

        activity.markdown(
            '<div class="execution-active">'
            '<span class="activity-dot"></span>'
            '<strong>Processing · Batch execution active</strong>'
            '<span class="execution-copy">The governed pipeline is processing the catalogue. '
            'This may take several minutes.</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    completed: list[str] = []

    def update_progress(stage: str, detail: str) -> None:
        current_stage.markdown(f"**Current stage**  \n{stage}")
        completed_stages.markdown(
            "**Completed stages**  \n"
            + (" → ".join(completed) if completed else "None yet")
        )
        execution_detail.caption(
            f"{detail} · Elapsed {time.perf_counter() - started:.1f}s · "
            "Per-row progress is not exposed by the batch runtime."
        )

    try:
        update_progress("Preparing inputs", "Reading catalogue and delivery schema")
        with tempfile.TemporaryDirectory(prefix="unihack_catalogue_") as directory:
            root = Path(directory)
            catalogue_path = root / Path(catalogue_file.name).name
            catalogue_path.write_bytes(catalogue_file.getvalue())

            if schema_file is not None:
                local_schema_path = root / "delivery_schema.csv"
                local_schema_path.write_bytes(schema_file.getvalue())
                schema = load_delivery_schema(local_schema_path)
            elif schema_path is not None:
                schema = load_delivery_schema(schema_path)
            else:
                raise ValueError("An official delivery schema is required.")

            completed.append("Inputs")
            update_progress(
                "Discovery → Delivery",
                "The synchronous batch runtime is executing its governed pipeline",
            )
            search_provider = (
                SerperSearchProvider.from_environment()
                if discovery_enabled else None
            )
            batch = run_catalogue_batch(
                catalogue_path,
                schema,
                discovery_enabled=discovery_enabled,
                runtime_policy_resolution_enabled=discovery_enabled,
                search_provider=search_provider,
            )

        completed.extend(["Discovery", "Verification", "Identification", "Enrichment", "Validation", "Delivery"])
        activity.success("Processing complete · Batch execution finished")
        update_progress(
            "Complete",
            f"Processed {batch.processed_rows:,} of {batch.total_rows:,} products",
        )
        return batch
    except Exception as error:
        activity.error("Processing stopped · Batch execution failed")
        update_progress("Failed", "The run stopped before completion")
        st.error(f"The enrichment run could not be completed: {error}")
        return None


def _render_results_page() -> None:
    st.markdown("# Results")
    batch = _require_batch()
    if batch is None:
        return
    _render_summary_metrics(batch)

    search = st.text_input("Search MPN or manufacturer", placeholder="e.g. PDSH4816AF")
    status_filter = st.multiselect(
        "Status",
        ["ready", "needs_review", "blocked", "failed"],
        default=[],
    )
    rows = build_result_rows(batch)
    filtered = [
        row for row in rows
        if (not search or search.casefold() in f"{row['MPN']} {row['Manufacturer']}".casefold())
        and (not status_filter or row["Status"] in status_filter)
    ]
    st.dataframe(filtered, hide_index=True, use_container_width=True)

    if not filtered:
        st.info("No catalogue rows match the current filters.")
        return
    selected_mpn = st.selectbox("Open product detail", [row["MPN"] for row in filtered])
    selected = next(row for row in batch.row_results if row.catalogue_row.Mfg_Part_Num == selected_mpn)
    _render_product_detail(selected)


def _render_review_page() -> None:
    st.markdown("# Review queue")
    batch = _require_batch()
    if batch is None:
        return
    review_rows = build_review_rows(batch)
    if not review_rows:
        st.success("No rows currently require review.")
        return
    st.caption("Review issues are diagnostic; blocked values remain out of delivery output.")
    st.dataframe(review_rows, hide_index=True, use_container_width=True)

    selected_mpn = st.selectbox("Inspect review item", sorted({row["MPN"] for row in review_rows}))
    selected = next(row for row in batch.row_results if row.catalogue_row.Mfg_Part_Num == selected_mpn)
    _render_issue_list(selected)


def _render_delivery_page() -> None:
    st.markdown("# Delivery output")
    batch = _require_batch()
    if batch is None:
        return
    st.caption("This preview is the safely gated delivery output produced by the batch API.")
    if batch.delivery_rows:
        st.dataframe(batch.delivery_rows, hide_index=True, use_container_width=True)
        st.download_button(
            "Download enriched delivery CSV",
            data=delivery_csv_bytes(batch),
            file_name="unihack_delivery_output.csv",
            mime="text/csv",
            type="primary",
        )
        st.download_button(
            "Download candidate telemetry CSV",
            data=candidate_telemetry_csv_bytes(batch),
            file_name="candidate_telemetry.csv",
            mime="text/csv",
        )
    else:
        st.info("No delivery rows are available yet.")


def _render_product_detail(result: Any) -> None:
    row = result.catalogue_row
    st.divider()
    st.markdown(f"## {row.Mfg_Part_Num}")
    product = result.pipeline_result.product_identification if result.pipeline_result else None
    identity, source, state = st.columns(3)
    identity.metric("Product", product.product_type if product else "Not identified")
    source.metric("Verified sources", str(sum(d.success for d in result.source_diagnostics)))
    state.metric("Final status", result.review.status.replace("_", " ").upper())

    st.write(f"**Raw manufacturer:** {row.Part_Manuf}")
    verified_urls = [d.url for d in result.source_diagnostics if d.success]
    if verified_urls:
        st.markdown("**Verified source(s):** " + "  \n".join(verified_urls))
    if result.reference_resolution is not None:
        resolved = result.reference_resolution.manufacturer.resolved_value
        st.write(f"**Resolved manufacturer:** {resolved or 'Unresolved'}")
        runtime = result.reference_resolution.runtime_identity
        if runtime is not None:
            st.caption(f"Row-scoped runtime identity: {runtime.resolved_value}")
    if product:
        st.write(f"**Category:** {product.product_category}")

    if result.pipeline_result:
        st.subheader("Evidence-backed attributes")
        _render_validation_attributes(result.pipeline_result)
    else:
        st.info("No completed pipeline result is available for this row.")

    _render_issue_list(result)
    st.subheader("Delivery slots")
    slot_rows = [
        {
            "Slot": n,
            "Attribute": result.delivery_row.get(f"ATTRIBUTE_LABEL {n}", ""),
            "Value": result.delivery_row.get(f"ATTRIBUTE_VALUE {n}", ""),
            "UOM": result.delivery_row.get(f"ATTRIBUTE_UOM {n}", ""),
        }
        for n in range(1, 51)
        if result.delivery_row.get(f"ATTRIBUTE_VALUE {n}", "")
    ]
    st.dataframe(slot_rows, hide_index=True, use_container_width=True)


def _render_validation_attributes(pipeline_result: Any) -> None:
    labels = {item.name: item.label for item in pipeline_result.dynamic_attribute_schema}
    confidence = {item.name: item for item in pipeline_result.confidence.attributes}
    for attribute in pipeline_result.validation.attributes:
        label = labels.get(attribute.name, attribute.name)
        assessment = confidence.get(attribute.name)
        with st.expander(f"{label} · {attribute.status}"):
            if not attribute.values:
                st.write("Not found")
                continue
            for value in attribute.values:
                evidence = value.evidence
                st.markdown(f"**Value:** {value.value}")
                st.markdown(
                    f"**Source:** `{evidence.source_name}`  \n"
                    f"**Location:** `{evidence.location or 'document'}`  \n"
                    f"**Quote:** “{evidence.quote}”"
                )
            if assessment:
                st.caption(
                    f"Confidence: {assessment.level} ({assessment.score:.2f}) — "
                    f"{' · '.join(assessment.reasons)}"
                )


def _render_issue_list(result: Any) -> None:
    if not result.review.issues:
        st.success("No review issues.")
        return
    st.subheader("Diagnostics")
    for issue in result.review.issues:
        if issue.severity in {"blocking", "error"}:
            st.error(f"{issue.code}: {issue.message}")
        else:
            st.warning(f"{issue.code}: {issue.message}")


def _render_summary_metrics(batch: BatchResult) -> None:
    cols = st.columns(5)
    cols[0].metric("Processed", batch.processed_rows)
    cols[1].metric("Ready", batch.ready_rows)
    cols[2].metric("Needs review", batch.needs_review_rows)
    cols[3].metric("Blocked", batch.blocked_rows)
    cols[4].metric("Failed", batch.failed_rows)


def build_result_rows(batch: BatchResult) -> list[dict[str, Any]]:
    """Create searchable result rows without changing backend models."""
    rows = []
    for result in batch.row_results:
        verified_urls = [d.url for d in result.source_diagnostics if d.success]
        rows.append({
            "MPN": result.catalogue_row.Mfg_Part_Num,
            "Manufacturer": result.catalogue_row.Part_Manuf,
            "Verified source": verified_urls[0] if verified_urls else "None",
            "Verified sources": sum(d.success for d in result.source_diagnostics),
            "Accepted attributes": sum(d.status == "mapped" for d in result.mapping_diagnostics),
            "Status": result.review.status,
        })
    return rows


def build_attribute_rows(result: Any) -> list[dict[str, str]]:
    """Format validated product attributes for tables and compatibility views."""
    labels = {attribute.name: attribute.label for attribute in result.dynamic_attribute_schema}
    confidence = {assessment.name: assessment for assessment in result.confidence.attributes}
    rows: list[dict[str, str]] = []
    for attribute in result.validation.attributes:
        assessment = confidence.get(attribute.name)
        value = "Not found" if not attribute.values else " / ".join(item.value for item in attribute.values)
        status = "⚠ Conflict" if attribute.status == "conflict" else _status_label(attribute.status)
        rows.append({
            "Attribute": labels.get(attribute.name, attribute.name),
            "Value": value,
            "Status": status,
            "Confidence": (
                f"{assessment.level.title()} ({assessment.score:.2f})"
                if assessment is not None else "Unavailable"
            ),
        })
    return rows


def build_conflict_rows(result: Any) -> list[dict[str, str]]:
    """Format conflicts while preserving every source value."""
    labels = {attribute.name: attribute.label for attribute in result.dynamic_attribute_schema}
    return [
        {
            "Attribute": labels.get(attribute.name, attribute.name),
            "Values": " / ".join(item.value for item in attribute.values),
        }
        for attribute in result.validation.attributes
        if attribute.status == "conflict"
    ]


def _status_label(status: str) -> str:
    return {
        "consistent": "Consistent",
        "single_source": "Single source",
        "not_found": "Not found",
    }.get(status, status.replace("_", " ").title())


def build_review_rows(batch: BatchResult) -> list[dict[str, Any]]:
    """Flatten row issues for the review screen."""
    rows = []
    for result in batch.row_results:
        for issue in result.review.issues:
            if result.review.status in {"needs_review", "blocked", "failed"}:
                rows.append({
                    "MPN": result.catalogue_row.Mfg_Part_Num,
                    "Status": result.review.status,
                    "Severity": issue.severity,
                    "Code": issue.code,
                    "Reason": issue.message,
                })
    return rows


def delivery_csv_bytes(batch: BatchResult) -> bytes:
    """Serialize the already schema-validated batch delivery rows."""
    if not batch.delivery_rows:
        return b""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(batch.delivery_rows[0].keys()))
    writer.writeheader()
    writer.writerows(batch.delivery_rows)
    return output.getvalue().encode("utf-8")


def build_candidate_telemetry_rows(batch: BatchResult) -> list[dict[str, Any]]:
    """Flatten bounded candidate diagnostics without including source content."""
    rows: list[dict[str, Any]] = []
    for item in batch.candidate_telemetry:
        telemetry = item.telemetry
        ranking = telemetry.ranking
        rows.append({
            "MPN": item.mfg_part_num,
            "candidate_url": telemetry.url,
            "domain": telemetry.domain,
            "ranking": ranking.decision if ranking is not None else "",
            "decision": ranking.decision if ranking is not None else "",
            "score": ranking.score if ranking is not None else "",
            "fetched": telemetry.fetched,
            "http_status": telemetry.http_status if telemetry.http_status is not None else "",
            "content_type": telemetry.content_type or "",
            "exact_mpn": (
                telemetry.exact_mpn_verified
                if telemetry.exact_mpn_verified is not None else ""
            ),
            "identity_value": telemetry.identity_value or "",
            "identity_kind": telemetry.identity_kind or "",
            "identity_result": telemetry.identity_result or "",
            "rejection_code": telemetry.rejection_code or "",
        })
    return rows


def candidate_telemetry_csv_bytes(batch: BatchResult) -> bytes:
    """Serialize candidate diagnostics separately from result/review CSVs."""
    fieldnames = [
        "MPN", "candidate_url", "domain", "ranking", "decision", "score",
        "fetched", "http_status", "content_type", "exact_mpn",
        "identity_value", "identity_kind", "identity_result", "rejection_code",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(build_candidate_telemetry_rows(batch))
    return output.getvalue().encode("utf-8")


def _get_batch() -> BatchResult | None:
    value = st.session_state.get("catalogue_batch_result")
    return value if isinstance(value, BatchResult) else None


def _require_batch() -> BatchResult | None:
    batch = _get_batch()
    if batch is None:
        _render_empty_state("No run loaded", "Return to Run and upload a catalogue to populate this workspace.")
    return batch


def _render_empty_state(title: str, message: str) -> None:
    st.markdown(
        f'<div class="empty-state"><div class="empty-kicker">WORKSPACE</div>'
        f'<h3>{title}</h3><p>{message}</p></div>',
        unsafe_allow_html=True,
    )


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root { --ink: #e8edf5; --muted: #8e9aae; --panel: #111923; --line: #253244; --accent: #55d6be; }
        .stApp { background: #0b1118; color: var(--ink); }
        [data-testid="stSidebar"] { background: #0d151f; border-right: 1px solid var(--line); }
        [data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--line); padding: 14px; border-radius: 8px; }
        .pipeline-stage { min-height: 92px; padding: 12px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel); }
        .pipeline-stage.complete { border-color: #2c756d; }
        .pipeline-stage.running { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(85,214,190,.12); }
        .stage-marker { color: var(--accent); font-size: .72rem; font-weight: 700; letter-spacing: .08em; }
        .stage-name { color: var(--ink); font-weight: 700; margin-top: 7px; }
        .stage-detail { color: var(--muted); font-size: .72rem; margin-top: 3px; line-height: 1.25; }
        .execution-active { display: flex; align-items: center; gap: 9px; padding: 10px 12px; margin-bottom: 12px; border: 1px solid #2c756d; border-radius: 8px; background: rgba(85,214,190,.07); color: var(--ink); }
        .execution-copy { color: var(--muted); font-size: .82rem; }
        .activity-dot { width: 10px; height: 10px; flex: 0 0 10px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 0 rgba(85,214,190,.55); animation: execution-pulse 1.5s infinite; }
        @keyframes execution-pulse { 0% { box-shadow: 0 0 0 0 rgba(85,214,190,.55); opacity: 1; } 70% { box-shadow: 0 0 0 9px rgba(85,214,190,0); opacity: .72; } 100% { box-shadow: 0 0 0 0 rgba(85,214,190,0); opacity: 1; } }
        .empty-state { border: 1px solid var(--line); background: var(--panel); border-radius: 10px; padding: 32px; margin-top: 24px; }
        .empty-kicker { color: var(--accent); font-size: 0.72rem; letter-spacing: 0.16em; font-weight: 700; }
        .empty-state h3 { margin: 8px 0 4px 0; }
        .empty-state p { color: var(--muted); margin: 0; }
        div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
