import { describe, expect, it } from "vitest";
import {
  bridgeCommandErrorNotificationMessage,
  bridgeServerErrorMessage,
  isStaleBridgeClientError,
} from "./bridgeErrors";

describe("bridge error helpers", () => {
  it("maps stale client server errors to user-facing text", () => {
    expect(isStaleBridgeClientError("Client was not found")).toBe(true);
    expect(isStaleBridgeClientError("RECONNECT_REQUIRED")).toBe(true);
    expect(
      bridgeServerErrorMessage("Client was not found", {
        staleClient: "连接已过期，请刷新页面后重试",
        fallback: "发送 bridge 消息失败",
      }),
    ).toBe("连接已过期，请刷新页面后重试");
  });

  it("keeps other server details and falls back for empty details", () => {
    expect(
      bridgeServerErrorMessage("bad request", {
        staleClient: "连接已过期",
        fallback: "发送 bridge 消息失败",
      }),
    ).toBe("bad request");
    expect(
      bridgeServerErrorMessage("", {
        staleClient: "连接已过期",
        fallback: "发送 bridge 消息失败",
      }),
    ).toBe("发送 bridge 消息失败");
  });

  it("surfaces bridge command errors as notification text", () => {
    expect(
      bridgeCommandErrorNotificationMessage(
        {
          type: "command_error",
          error: "No API key found for xiaomi-token-plan-cn.\nUse /login",
        },
        "发送 bridge 消息失败",
      ),
    ).toBe("No API key found for xiaomi-token-plan-cn.");

    expect(
      bridgeCommandErrorNotificationMessage(
        {
          type: "transcript_delta",
          error: "not a command error",
        },
        "发送 bridge 消息失败",
      ),
    ).toBeNull();
  });
});
