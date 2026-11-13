/** @vitest-environment happy-dom */
import { afterEach, expect, it } from "vitest";
import { createAppNavigation } from "./appNavigation.svelte";
afterEach(() => { window.history.replaceState(null, "", "/"); });
it("uses a real help URL without adding duplicate entries and preserves the chat return address", () => {
  window.history.replaceState({ existing: true }, "", "/chat?session=one#draft");
  const navigation = createAppNavigation();
  navigation.openHelp();
  expect(window.location.pathname).toBe("/help");
  expect(navigation.isHelp).toBe(true);
  expect(window.history.state).toMatchObject({ existing: true, danoHelpReturnTo: "/chat?session=one#draft" });
  const count = window.history.length;
  navigation.openHelp();
  expect(window.history.length).toBe(count);
  window.history.replaceState(null, "", "/chat?session=one#draft"); navigation.sync();
  expect(navigation.isHelp).toBe(false);
});
it("recognizes a directly opened help page and returns to chat without needing prior app history", () => {
  window.history.replaceState(null, "", "/help");
  const navigation = createAppNavigation();
  expect(navigation.isHelp).toBe(true);
  navigation.returnToChat();
  expect(window.location.pathname).toBe("/");
  expect(navigation.isHelp).toBe(false);
});
it("routes same-tab help links while retaining normal modified-click and new-tab behavior", () => {
  window.history.replaceState(null, "", "/chat");
  const navigation = createAppNavigation();
  const anchor = document.createElement("a"); anchor.href = "/help"; anchor.target = "_self";
  document.body.append(anchor);
  const routed: boolean[] = [];
  anchor.addEventListener("click", event => {
    navigation.handleLinkClick(event);
    routed.push(event.defaultPrevented);
    // Unhandled links retain their browser behavior; avoid actual fetches in happy-dom.
    event.preventDefault();
  });
  try {
    const modified = new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
    anchor.dispatchEvent(modified); expect(routed.shift()).toBe(false);
    anchor.target = "_blank";
    const blank = new MouseEvent("click", { bubbles: true, cancelable: true });
    anchor.dispatchEvent(blank); expect(routed.shift()).toBe(false);
    anchor.target = "_self";
    const regular = new MouseEvent("click", { bubbles: true, cancelable: true });
    anchor.dispatchEvent(regular); expect(routed.shift()).toBe(true);
    expect(navigation.isHelp).toBe(true);
    expect(window.location.pathname).toBe("/help");
  } finally { anchor.remove(); }
});
