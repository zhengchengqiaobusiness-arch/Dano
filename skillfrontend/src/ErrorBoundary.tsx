import React from "react";

interface S { err: Error | null }

// 顶层错误边界:任何页面渲染崩了都显示错误信息,而不是白屏(再不"全部为空 进不去")。
export default class ErrorBoundary extends React.Component<{ children: React.ReactNode }, S> {
  state: S = { err: null };
  static getDerivedStateFromError(err: Error): S { return { err }; }
  componentDidCatch(err: Error, info: React.ErrorInfo) { console.error("page crashed:", err, info); }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div style={{ padding: 24, fontFamily: "Consolas, monospace" }}>
        <h2 style={{ color: "#cf1322" }}>页面渲染出错</h2>
        <pre style={{ whiteSpace: "pre-wrap", background: "#fff1f0", padding: 12, borderRadius: 6 }}>
          {String(this.state.err && (this.state.err.stack || this.state.err.message))}
        </pre>
        <button onClick={() => { this.setState({ err: null }); location.assign("/settings"); }}>回到运行配置</button>
      </div>
    );
  }
}
