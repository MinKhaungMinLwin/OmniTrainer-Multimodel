import { OmniApiClient } from "@omni/contracts";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App, ChatMessage, microphoneErrorMessage } from "./App";

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

test("renders the development sign-in experience", () => {
  render(<App />);
  expect(
    screen.getByRole("heading", { name: /run your operation/i }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
});

test("explains how to recover when microphone permission is blocked", () => {
  expect(
    microphoneErrorMessage(
      new DOMException("Permission denied", "NotAllowedError"),
    ),
  ).toMatch(/allow Microphone/i);
});

test("read-aloud playback can be interrupted", async () => {
  const pause = vi.fn();
  const play = vi.fn().mockResolvedValue(undefined);
  class TestAudio {
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onpause: (() => void) | null = null;
    pause = pause;
    play = play;
    removeAttribute = vi.fn();
  }
  vi.stubGlobal("Audio", TestAudio);
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:test-audio"),
    revokeObjectURL: vi.fn(),
  });
  const api = {
    synthesizeSpeech: vi.fn().mockResolvedValue(new Blob(["wav"])),
  } as unknown as OmniApiClient;

  render(
    <ChatMessage
      api={api}
      message={{
        id: "message-1",
        conversation_id: "conversation-1",
        run_id: "run-1",
        role: "assistant",
        content: "Hello from Omni.",
        parts: [],
        citations: [],
        created_at: new Date().toISOString(),
      }}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
  await screen.findByRole("button", { name: "Stop read aloud" });
  await waitFor(() => expect(play).toHaveBeenCalledOnce());

  fireEvent.click(screen.getByRole("button", { name: "Stop read aloud" }));
  expect(pause).toHaveBeenCalled();
  await screen.findByRole("button", { name: "Read aloud" });
});
