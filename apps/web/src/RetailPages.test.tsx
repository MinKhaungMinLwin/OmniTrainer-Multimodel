import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { formatMoney, isRestockLines, RestockLinesTable } from "./RetailPages";

const lines = [
  {
    sku: "RW-UMB-CMP",
    name: "StormShield Compact Umbrella",
    quantity: 156,
    on_hand: 32,
    forecast_units: 91.4,
    stockout_date: "2026-10-16",
    risk: "critical" as const,
    supplier: "Kaze Rainwear",
  },
  { sku: "TA-PWRBANK", name: "20,000mAh Power Bank", quantity: 72 },
];

describe("RestockLinesTable", () => {
  it("edits one line's order quantity without touching the others", () => {
    const onChange = vi.fn();
    render(<RestockLinesTable lines={lines} onChange={onChange} />);
    expect(screen.getByText("▲ Stock-out before restock")).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("Order quantity for StormShield Compact Umbrella"),
      { target: { value: "200" } },
    );
    expect(onChange).toHaveBeenCalledWith([
      { ...lines[0], quantity: 200 },
      lines[1],
    ]);
  });

  it("recognises restock lines and formats SGD", () => {
    expect(isRestockLines(lines)).toBe(true);
    expect(isRestockLines("[]")).toBe(false);
    expect(isRestockLines([{ name: "no sku" }])).toBe(false);
    expect(formatMoney(2490)).toContain("24.90");
  });
});
