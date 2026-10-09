import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ViewNotice } from "../components/ViewNotice";
import { reportFx } from "../test/fixtures";

describe("ViewNotice", () => {
  it("renders when side_on is false", () => {
    render(<ViewNotice view={reportFx.view} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/not side-on/i);
  });

  it("renders nothing when side_on is true", () => {
    const { container } = render(
      <ViewNotice view={{ ...reportFx.view, class: "side", side_on: true, notice: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
