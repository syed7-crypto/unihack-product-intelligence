import csv
import tempfile
import unittest
from pathlib import Path

from src.product_intelligence.catalog_input import CatalogInputRow, INPUT_COLUMNS
from src.product_intelligence.catalogue_batch import run_catalogue_batch
from src.product_intelligence.catalogue_enrichment import (
    CatalogueEnrichmentResult,
    EvaluationComparison,
)
from src.product_intelligence.delivery_schema import DeliverySchema
from src.product_intelligence.review import ReviewIssue, ReviewReport


def row(mpn: str) -> CatalogInputRow:
    return CatalogInputRow(
        Mfg_Part_Num=mpn,
        Part_Desc=f"Description {mpn}",
        E1_Brand="Brand",
        Unilog_Brand="Brand",
        DIB_Brand="Brand",
        Part_Manuf="Manufacturer",
    )


def schema() -> DeliverySchema:
    return DeliverySchema(
        (*INPUT_COLUMNS, "MFR URL", "ATTRIBUTE_LABEL 1", "ATTRIBUTE_VALUE 1", "ATTRIBUTE_UOM 1")
    )


def result_for(catalogue_row: CatalogInputRow, delivery_schema: DeliverySchema, status: str) -> CatalogueEnrichmentResult:
    delivery = delivery_schema.empty_row()
    if status == "ready":
        delivery["ATTRIBUTE_LABEL 1"] = "Test Attribute"
        delivery["ATTRIBUTE_VALUE 1"] = "Approved Value"
    issue = []
    if status != "ready":
        issue.append(
            ReviewIssue(
                code=f"{status.upper()}_ISSUE",
                severity="blocking" if status == "blocked" else "warning",
                scope="row",
                message=f"{status} row",
                current_value=catalogue_row.Mfg_Part_Num,
                affects_delivery=True,
            )
        )
    comparison = (
        EvaluationComparison(
            mfg_part_num=catalogue_row.Mfg_Part_Num,
            matches=False,
            differences=[],
        )
        if catalogue_row.Mfg_Part_Num == "EVAL"
        else None
    )
    return CatalogueEnrichmentResult(
        catalogue_row=catalogue_row,
        pipeline_result=None,
        delivery_row=delivery,
        evaluation_comparison=comparison,
        review=ReviewReport(status=status, issues=issue),  # type: ignore[arg-type]
    )


class CatalogueBatchTests(unittest.TestCase):
    def test_three_rows_are_processed_in_order(self) -> None:
        rows = [row("A"), row("B"), row("C")]

        def enrich(catalogue_row, *_args, **_kwargs):
            return result_for(catalogue_row, schema(), "ready")

        result = run_catalogue_batch(rows, schema(), source_urls={}, row_enricher=enrich)

        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.processed_rows, 3)
        self.assertEqual([item.catalogue_row.Mfg_Part_Num for item in result.row_results], ["A", "B", "C"])
        self.assertEqual([item["Mfg_Part_Num"] for item in result.delivery_rows], ["A", "B", "C"])
        self.assertEqual(result.ready_rows, 3)

    def test_mixed_outcomes_are_isolated_and_unsafe_fields_are_blank(self) -> None:
        rows = [row("READY"), row("REVIEW"), row("BLOCKED"), row("FAIL")]

        def enrich(catalogue_row, *_args, **_kwargs):
            if catalogue_row.Mfg_Part_Num == "FAIL":
                raise RuntimeError("mock row failure")
            return result_for(
                catalogue_row,
                schema(),
                {"READY": "ready", "REVIEW": "needs_review", "BLOCKED": "blocked"}[catalogue_row.Mfg_Part_Num],
            )

        result = run_catalogue_batch(rows, schema(), source_urls={}, row_enricher=enrich)

        self.assertEqual((result.ready_rows, result.needs_review_rows, result.blocked_rows, result.failed_rows), (1, 1, 1, 1))
        self.assertEqual([item.catalogue_row.Mfg_Part_Num for item in result.row_results], ["READY", "REVIEW", "BLOCKED", "FAIL"])
        self.assertEqual(result.delivery_rows[0]["ATTRIBUTE_VALUE 1"], "Approved Value")
        for delivery in result.delivery_rows[1:]:
            self.assertEqual(delivery["ATTRIBUTE_VALUE 1"], "")
        self.assertEqual(result.row_results[-1].review.status, "failed")

    def test_duplicate_mpns_remain_independent_rows(self) -> None:
        rows = [row("DUP"), row("DUP")]
        calls = []

        def enrich(catalogue_row, *_args, **_kwargs):
            calls.append(catalogue_row.Part_Desc)
            return result_for(catalogue_row, schema(), "ready")

        result = run_catalogue_batch(rows, schema(), source_urls={"DUP": ["https://example.test/source"]}, row_enricher=enrich)

        self.assertEqual(len(result.row_results), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual([item["Mfg_Part_Num"] for item in result.delivery_rows], ["DUP", "DUP"])

    def test_empty_catalogue_has_zero_rows(self) -> None:
        result = run_catalogue_batch([], schema(), source_urls={})

        self.assertEqual(result.total_rows, 0)
        self.assertEqual(result.processed_rows, 0)
        self.assertEqual(result.row_results, [])
        self.assertEqual(result.delivery_rows, [])
        self.assertEqual(result.review_issues, [])

    def test_review_and_evaluation_diagnostics_keep_row_identity(self) -> None:
        rows = [row("REVIEW"), row("EVAL")]

        def enrich(catalogue_row, *_args, **_kwargs):
            return result_for(catalogue_row, schema(), "needs_review" if catalogue_row.Mfg_Part_Num == "REVIEW" else "ready")

        result = run_catalogue_batch(rows, schema(), source_urls={}, row_enricher=enrich)

        self.assertEqual(len(result.review_issues), 1)
        self.assertEqual(result.review_issues[0].row_index, 0)
        self.assertEqual(result.review_issues[0].mfg_part_num, "REVIEW")
        self.assertEqual(len(result.evaluation_diagnostics), 1)
        self.assertEqual(result.evaluation_diagnostics[0].row_index, 1)
        self.assertEqual(result.evaluation_diagnostics[0].mfg_part_num, "EVAL")

    def test_csv_input_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(INPUT_COLUMNS))
                writer.writeheader()
                writer.writerow(row("CSV-1").model_dump())

            def enrich(catalogue_row, *_args, **_kwargs):
                return result_for(catalogue_row, schema(), "ready")

            result = run_catalogue_batch(path, schema(), source_urls={}, row_enricher=enrich)

        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.row_results[0].catalogue_row.Mfg_Part_Num, "CSV-1")

    def test_default_missing_source_configuration_fails_each_row_without_dropping_it(self) -> None:
        result = run_catalogue_batch([row("A"), row("B")], schema())

        self.assertEqual(result.failed_rows, 2)
        self.assertEqual([item.review.status for item in result.row_results], ["failed", "failed"])
        self.assertEqual([item["Mfg_Part_Num"] for item in result.delivery_rows], ["A", "B"])
        self.assertTrue(all(item["ATTRIBUTE_VALUE 1"] == "" for item in result.delivery_rows))


if __name__ == "__main__":
    unittest.main()
