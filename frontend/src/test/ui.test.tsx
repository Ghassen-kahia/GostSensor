import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Reading, Sensor } from "../api";
import { ReadingValue, SourceBadge, VirtualBadge } from "../components";
import { ageAt, fmtDuration, fmtTime } from "../lib";
import { OperatorProvider, ToastProvider } from "../lib";
import Sensors from "../pages/Sensors";

const base: Reading = {
  sensorId: "T-02-14-N", type: "TEMPERATURE", unit: "degC", roomId: "02-14-N", sensorStatus: "ONLINE", anomaly: null, incidentId: null,
  simulatedFault: false, source: "PHYSICAL", value: 19.42, observedAt: "2026-03-02T06:00:00Z", inferredAt: null, modelVersion: null,
  uncertainty: null, inferenceQuality: null, lastPhysical: { value: 19.42, observedAt: "2026-03-02T06:00:00Z" },
  lastTransmissionAt: "2026-03-02T06:00:00Z", evaluatedAt: "2026-03-02T06:00:00Z",
};
const inferred: Reading = {
  ...base, sensorStatus: "OFFLINE", source: "INFERRED", value: 20.1, observedAt: null, inferredAt: "2026-03-02T09:00:00Z",
  modelVersion: "temp-rf-x", uncertainty: { lower: 19.3, upper: 20.9, level: 0.9, method: "conformal" }, inferenceQuality: "GOOD",
};

describe("physical vs inferred presentation", () => {
  it("labels physical readings as physical", () => {
    render(<SourceBadge r={base} />);
    expect(screen.getByText("Physical")).toBeInTheDocument();
    expect(screen.queryByText("Capteur Virtuel Actif")).toBeNull();
  });

  it("labels inferred readings with the virtual-sensor badge and shows the interval", () => {
    render(<><SourceBadge r={inferred} /><ReadingValue r={inferred} /></>);
    expect(screen.getByText("Capteur Virtuel Actif")).toBeInTheDocument();
    expect(screen.getByText(/±0\.8/)).toBeInTheDocument();
    expect(screen.getByTitle(/90 % interval/)).toBeInTheDocument();
  });

  it("never shows a value when the source is NONE", () => {
    render(<ReadingValue r={{ ...base, source: "NONE", value: null, sensorStatus: "OFFLINE" }} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("virtual badge has an accessible ghost icon", () => {
    render(<VirtualBadge />);
    expect(screen.getByRole("img", { name: /virtual sensor/i })).toBeInTheDocument();
  });
});

describe("formatting", () => {
  it("formats building time in UTC", () => {
    expect(fmtTime("2026-03-03T09:45:00Z")).toBe("2026-03-03 09:45 UTC");
  });
  it("computes ages against the building clock, not the browser clock", () => {
    expect(ageAt("2026-03-03T09:00:00Z", Date.parse("2026-03-03T10:30:00Z") / 1000)).toBe("1.5 h");
    expect(fmtDuration(3 * 86400)).toBe("3.0 d");
  });
});

describe("Sensors page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders rows from the API and marks inferred sensors", async () => {
    const sensor = (id: string, r: Reading): Sensor => ({
      id, externalId: `/x/${id}`, roomId: r.roomId, type: "TEMPERATURE", unit: "degC", displayName: null, floor: null, zone: null, station: null,
      stationSource: null, resolutionS: 900, staleAfterS: 2700, offlineAfterS: 7200, meta: {}, storey: "N2", reading: { ...r, sensorId: id },
    });
    const page = { items: [sensor("T-A", base), sensor("T-B", inferred)], total: 2, page: 1, pageSize: 50, statusCounts: { ONLINE: 1, OFFLINE: 1 }, sourceCounts: {} };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const body = String(url).startsWith("/api/clock") ? { mode: "REPLAY", now: 1772442000, speed: 60, paused: false, dataStart: 0, dataEnd: 1, atEnd: false, label: "" } : page;
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><ToastProvider><OperatorProvider><Sensors /></OperatorProvider></ToastProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("T-B")).toBeInTheDocument());
    const rowB = screen.getByText("T-B").closest("tr")!;
    expect(rowB.className).toContain("inferred");
    expect(rowB.textContent).toContain("Capteur Virtuel Actif");
    const rowA = screen.getByText("T-A").closest("tr")!;
    expect(rowA.textContent).toContain("Physical");
    expect(rowA.className).not.toContain("inferred");
  });
});

describe("assistant markdown", () => {
  it("renders bold, lists and links incident / room ids without raw HTML", async () => {
    const { Markdown } = await import("../Chat");
    const { container } = render(
      <MemoryRouter><Markdown text={"**2** open incidents:\n- INC-20260303-A00ACC in 02-14-N\n- <img src=x onerror=alert(1)>"} /></MemoryRouter>,
    );
    expect(container.querySelector("strong")?.textContent).toBe("2");
    expect(screen.getByRole("link", { name: "INC-20260303-A00ACC" })).toHaveAttribute("href", "/incidents/INC-20260303-A00ACC");
    expect(screen.getByRole("link", { name: "02-14-N" })).toHaveAttribute("href", "/twin?room=02-14-N");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});
