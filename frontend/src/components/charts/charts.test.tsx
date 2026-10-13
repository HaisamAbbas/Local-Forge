import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChartFigure } from "./ChartParts";
import { HistogramChart } from "./HistogramChart";
import { HorizontalBarChart } from "./HorizontalBarChart";
import { RequestPointChart } from "./RequestPointChart";
import { TimeSeriesChart } from "./TimeSeriesChart";

afterEach(cleanup);

describe("chart components", () => {
  it("renders one dot per request and reads values with the keyboard", () => {
    const { container } = render(
      <RequestPointChart
        ariaLabel="TTFT per request"
        points={[
          { key: "1", requestNumber: 2, value: 131.4 },
          { key: "2", requestNumber: 3, value: 111.6 },
        ]}
        unit="ms"
        valueLabel="TTFT"
        yLabel="TTFT (ms)"
      />,
    );
    const chart = screen.getByRole("img", { name: /TTFT per request\. 2 points/ });
    expect(container.querySelectorAll("circle.lf-chart__dot")).toHaveLength(2);
    expect(within(chart).getByText("TTFT (ms)")).toBeInTheDocument();
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText("Request 2, TTFT: 131.4 ms")).toBeInTheDocument();
    fireEvent.keyDown(chart, { key: "End" });
    expect(screen.getByText("Request 3, TTFT: 111.6 ms")).toBeInTheDocument();
    fireEvent.keyDown(chart, { key: "Escape" });
    expect(container.querySelector(".lf-chart__tooltip")).toBeNull();
  });

  it("draws histogram columns only for non-empty bins", () => {
    const { container } = render(
      <HistogramChart
        ariaLabel="Gap histogram"
        bins={[
          { start: 40, end: 50, count: 3 },
          { start: 50, end: 60, count: 0 },
          { start: 60, end: 70, count: 1 },
        ]}
        unit="ms"
        xLabel="Gap (ms)"
      />,
    );
    expect(screen.getByRole("img", { name: /4 values in 3 bins/ })).toBeInTheDocument();
    expect(container.querySelectorAll("path.lf-chart__bar")).toHaveLength(2);
  });

  it("keeps unavailable rows explicit and highlights the baseline", () => {
    const { container } = render(
      <HorizontalBarChart
        ariaLabel="TTFT comparison"
        rows={[
          { key: "a", label: "Run aaaaaaaa", value: 144.4, emphasis: true },
          { key: "b", label: "Run bbbbbbbb", value: 2894.5, range: [2000, 3100] },
          { key: "c", label: "Run cccccccc", value: null, reason: "insufficient samples" },
        ]}
        unit="ms"
        xLabel="TTFT (ms)"
      />,
    );
    expect(container.querySelectorAll("path.lf-chart__bar")).toHaveLength(2);
    expect(container.querySelector("path.is-emphasis")).not.toBeNull();
    expect(container.querySelector("path.is-muted")).not.toBeNull();
    expect(container.querySelectorAll(".lf-chart__whisker")).toHaveLength(1);
    expect(screen.getByText("Unavailable · insufficient samples")).toBeInTheDocument();
    expect(screen.getByText(/· baseline/)).toBeInTheDocument();
  });

  it("draws a line and area over time and exposes the latest sample", () => {
    const { container } = render(
      <TimeSeriesChart
        ariaLabel="Host CPU"
        points={[
          { key: "a", t: 1, value: 20 },
          { key: "b", t: 2, value: 35 },
        ]}
        unit="%"
        yLabel="%"
      />,
    );
    expect(container.querySelector("path.lf-chart__line")).not.toBeNull();
    expect(container.querySelector("path.lf-chart__area")).not.toBeNull();
    fireEvent.keyDown(screen.getByRole("img", { name: /Host CPU/ }), { key: "End" });
    expect(screen.getByText("2 s into run, 35 %")).toBeInTheDocument();
  });

  it("shows the empty state instead of placeholder marks and offers a data table", () => {
    const { rerender } = render(
      <ChartFigure
        empty="No measured request has produced a first output yet."
        id="empty"
        table={{ caption: "Values", columns: ["Request", "Value"], rows: [] }}
        title="Time to first token per request"
      >
        <svg data-testid="never" />
      </ChartFigure>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No measured request");
    expect(screen.queryByTestId("never")).toBeNull();
    expect(screen.getByRole("figure", { name: "Time to first token per request" })).toBeVisible();

    rerender(
      <ChartFigure
        caption="Formula: x"
        id="full"
        table={{
          caption: "Values",
          columns: ["Request", "Value"],
          rows: [{ key: "1", cells: [2, "131.4 ms"] }],
        }}
        title="Time to first token per request"
      >
        <svg />
      </ChartFigure>,
    );
    const table = screen.getByRole("table", { hidden: true, name: "Values" });
    expect(within(table).getByRole("rowheader", { hidden: true, name: "2" })).toBeInTheDocument();
    expect(screen.getByText("Formula: x")).toBeInTheDocument();
  });
});
