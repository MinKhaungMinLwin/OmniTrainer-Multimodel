import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "./App";

beforeEach(() => localStorage.clear());

test("renders the development sign-in experience", () => {
  render(<App />);
  expect(
    screen.getByRole("heading", { name: /run your operation/i }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
});
