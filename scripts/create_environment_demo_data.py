from pathlib import Path

from openpyxl import Workbook


def main() -> None:
    destination = Path("data/environment/projects/demo-site/laboratory-results.xlsx")
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Results"
    sheet.append(["sample_id", "analyte", "result", "unit", "reporting_limit", "qualifier"])
    sheet.append(["GW-01", "PFOS", 0.11, "ug/L", 0.01, None])
    sheet.append(["GW-01", "PFOA", 0.32, "ug/L", 0.01, None])
    sheet.append(["GW-02", "Benzene", "<0.0005", "mg/L", 0.0005, "<"])
    sheet.append(["GW-02", "Lead", 0.004, "mg/L", 0.001, None])
    workbook.save(destination)
    print(f"Created synthetic demonstration workbook: {destination}")


if __name__ == "__main__":
    main()
